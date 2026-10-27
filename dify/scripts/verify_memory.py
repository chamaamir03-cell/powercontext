"""Real PowerContext integration check using clearly marked non-model test replies."""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import uuid
import httpx


def main():
    client = httpx.Client(base_url="http://127.0.0.1:8090", timeout=40, trust_env=False,
                          headers={"X-Game-Token": os.environ["GAME_TOKEN"]})
    player = "component-" + uuid.uuid4().hex[:12]
    turn_id = uuid.uuid4().hex

    def post(path, body):
        response = client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    first = post("/turns/prepare", {"player_code": player, "session_id": "component-session-a",
                                    "turn_id": turn_id, "query": "偷拿蓝伞"})
    assert first["memory_count"] == 0
    committed = post("/turns/commit", {"player_code": player, "turn_id": turn_id,
                                       "reply": "【组件测试固定台词，并非模型生成】蓝伞这笔账记下了。"})
    second = post("/turns/prepare", {"player_code": player, "session_id": "component-session-b",
                                     "turn_id": uuid.uuid4().hex, "query": "查看旧账"})
    context = json.loads(second["model_context"])
    assert second["scope_id"] == first["scope_id"]
    assert second["memory_count"] == 1 and second["citations"]
    assert "蓝伞" in context["旧账事实"][0]
    replayed = post("/turns/commit", {"player_code": player, "turn_id": turn_id,
                                      "reply": "重试固定台词，应当被忽略。"})
    assert replayed["replayed"] and replayed["reply"] == committed["reply"]
    isolated = post("/turns/prepare", {"player_code": player + "-other", "session_id": "component-session-c",
                                       "turn_id": uuid.uuid4().hex, "query": "查看旧账"})
    assert isolated["memory_count"] == 0 and isolated["scope_id"] != first["scope_id"]
    result = {"tested_at_utc": datetime.now(timezone.utc).isoformat(), "passed": True,
              "test_kind": "real PowerContext API integration; fixed test reply, not a Dify/model run",
              "player_code": player, "scope_id": first["scope_id"], "saved_memory_ref": committed["memory_ref"],
              "new_session_recalled_facts": context["旧账事实"], "idempotency_passed": True,
              "player_isolation_passed": True}
    path = Path("/app/runtime/memory-integration.json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
