"""Exercise the published Dify app, then inspect the real PowerContext-backed ledger."""
from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from provision import read_env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://npc-dify")
    parser.add_argument("--game-url", default="http://npc-game:8090")
    parser.add_argument("--env-file", type=Path, default=Path("/run/npc.env"))
    parser.add_argument("--app-config", type=Path, default=Path("/app/runtime/app.json"))
    parser.add_argument("--output", type=Path, default=Path("/app/runtime/verification.json"))
    args = parser.parse_args()
    env = read_env(args.env_file)
    configured = json.loads(args.app_config.read_text())
    client = httpx.Client(timeout=300, trust_env=False)
    run_id = uuid.uuid4().hex[:12]
    player, isolated = "judge-" + run_id, "isolated-" + run_id
    report = {"tested_at_utc": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
              "dify_version": "1.18.0", "powercontext_version": "1.4.1",
              "model": env["MODEL_NAME"], "app_id": configured["app_id"],
              "player_code": player, "isolated_player_code": isolated, "steps": []}

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def inspect(code):
        response = client.get(args.game_url + "/state/" + code, headers={"X-Game-Token": env["GAME_TOKEN"]})
        response.raise_for_status()
        return response.json()

    def chat(query, code=player, conversation=""):
        started = time.monotonic()
        payload = {"inputs": {"player_code": code}, "query": query, "response_mode": "blocking",
                   "user": "verification-" + code, "conversation_id": conversation}
        response = client.post(args.base_url + "/v1/chat-messages", json=payload,
                               headers={"Authorization": "Bearer " + configured["api_key"]})
        if response.is_error:
            # Keep raw diagnostics local. They are not included in the submitted evidence.
            (args.output.parent / "last-dify-error.json").write_text(response.text, encoding="utf-8")
            raise RuntimeError(f"Dify chat request failed with HTTP {response.status_code}; see local last-dify-error.json")
        result = response.json()
        assert result.get("answer", "").strip(), "The Dify application returned no answer"
        step = {"input": query, "player_code": code, "conversation_id": result["conversation_id"],
                "message_id": result.get("message_id"), "answer": result["answer"],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "workflow_run_id": (result.get("metadata") or {}).get("workflow_run_id")}
        report["steps"].append(step)
        save()
        print(f"Completed {query!r}: {step['elapsed_seconds']} s", flush=True)
        return result

    initial = inspect(player)
    assert initial["memory_count"] == 0
    first = chat("偷拿蓝伞")
    ledger = inspect(player)
    assert ledger["state"]["umbrella"] == "玩家拿走未还" and ledger["memory_count"] == 1
    recalled = chat("查看旧账")  # Omit conversation_id deliberately: actual new Dify conversation.
    assert recalled["conversation_id"] != first["conversation_id"]
    # Check the model's dialogue, excluding the deterministic inventory footer.
    narrative = recalled["answer"].split("\n---\n", 1)[0]
    assert "伞" in narrative and any(word in narrative for word in ["拿", "还", "借", "偷", "上次", "之前"]), \
        "Expected the model in the new conversation to recall the umbrella incident"
    assert "奶茶" not in narrative and "优惠券" not in narrative, "The model invented a gift that has not happened"
    report["cross_conversation_recall"] = {
        "passed": True, "first_conversation": first["conversation_id"],
        "new_conversation": recalled["conversation_id"], "scope_id": ledger["scope_id"],
        "saved_memory_ref": ledger["records"][0]["artifact"],
    }
    returned = chat("归还蓝伞", conversation=recalled["conversation_id"])
    gifted = chat("送奶茶", conversation=returned["conversation_id"])
    assert "券" in gifted["answer"].split("\n---\n", 1)[0], "The model should announce the new coupon"
    ledger = inspect(player)
    assert ledger["state"]["coupon"] and ledger["state"]["favor"] == 3
    report["hidden_ending"] = {"passed": True, "state": ledger["state"], "turn": ledger["turn"]}
    stranger = chat("查看旧账", code=isolated)
    narrative = stranger["answer"].split("\n---\n", 1)[0]
    assert not any(word in narrative for word in ["还记得", "上次", "那次", "那天"]), \
        "A new player must not receive invented shared memories"
    assert "玩家原话" not in narrative, "The model must not write additional player dialogue"
    other = inspect(isolated)
    assert other["scope_id"] != ledger["scope_id"]
    assert other["state"]["grudge"] == 0 and other["state"]["favor"] == 0
    assert other["memory_count"] == 1
    report["player_isolation"] = {"passed": True, "other_scope_id": other["scope_id"], "state": other["state"]}
    # Keep a pre-reset snapshot so service restart verification has non-trivial state.
    report["ledger_before_restart"] = ledger
    report["initial_state"] = initial["state"]
    report["passed"] = True
    save()
    print("PASS: published Dify app, new-conversation recall, relationship repair, hidden ending and player isolation.")


if __name__ == "__main__":
    main()
