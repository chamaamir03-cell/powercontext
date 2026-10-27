"""Restart only this game's services and verify the durable ledger on the host."""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener
from urllib.error import HTTPError

from bootstrap import APP, ROOT, read_env


def main():
    env = read_env(APP / ".env")
    evidence = APP / ".runtime" / "verification.json"
    report = json.loads(evidence.read_text(encoding="utf-8"))
    url = f"http://127.0.0.1:{env['GAME_PORT']}/state/{report['player_code']}"
    opener = build_opener(ProxyHandler({}))

    def inspect():
        request = Request(url, headers={"X-Game-Token": env["GAME_TOKEN"]})
        with opener.open(request, timeout=30) as response:
            return json.load(response)

    before = inspect()
    subprocess.run(["docker", "compose", "--env-file", str(APP / ".env"), "-f", str(APP / "compose.yaml"),
                    "stop", "memory"], cwd=ROOT, check=True)
    prepare_url = f"http://127.0.0.1:{env['GAME_PORT']}/turns/prepare"
    request = Request(prepare_url,
                      data=json.dumps({"player_code": report["player_code"], "session_id": "memory-outage-check",
                                       "turn_id": uuid.uuid4().hex, "query": "送奶茶"}).encode(),
                      headers={"Content-Type": "application/json", "X-Game-Token": env["GAME_TOKEN"]})
    try:
        try:
            with opener.open(request, timeout=35):
                raise AssertionError("The game must not fake a successful turn without PowerContext")
        except HTTPError as error:
            failure = json.load(error)
            assert error.code == 503 and failure.get("saved") is False
            report["memory_outage"] = {"passed": True, "http_status": error.code,
                                       "saved": failure["saved"], "detail": failure.get("detail")}
    finally:
        subprocess.run(["docker", "compose", "--env-file", str(APP / ".env"), "-f", str(APP / "compose.yaml"),
                        "restart", "game", "memory"], cwd=ROOT, check=True)
    for attempt in range(60):
        try:
            after = inspect()
            break
        except Exception:
            if attempt == 59:
                raise
            time.sleep(2)
    assert before == after, "Restart changed or lost the authoritative PowerContext ledger"
    report["restart_persistence"] = {"passed": True, "scope_id": after["scope_id"],
                                     "memory_count": after["memory_count"], "state": after["state"]}
    configured = json.loads((APP / ".runtime" / "app.json").read_text())
    request = Request(f"http://127.0.0.1:{env['DIFY_PORT']}/v1/chat-messages",
                      data=json.dumps({"inputs": {"player_code": report["player_code"]},
                                       "query": "查看旧账", "conversation_id": "",
                                       "response_mode": "blocking",
                                       "user": "verification-" + report["player_code"]}).encode(),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + configured["api_key"]})
    with opener.open(request, timeout=300) as response:
        chat = json.load(response)
    assert chat.get("answer") and chat["conversation_id"] not in [step["conversation_id"] for step in report["steps"]]
    continued = inspect()
    assert continued["state"] == before["state"] and continued["memory_count"] == before["memory_count"] + 1
    report["chat_after_restart"] = {"passed": True, "conversation_id": chat["conversation_id"],
                                     "message_id": chat.get("message_id"), "answer": chat["answer"],
                                     "memory_count": continued["memory_count"]}
    evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS: unavailable memory cannot fake a save; same scope and game state after service restart.")


if __name__ == "__main__":
    main()
