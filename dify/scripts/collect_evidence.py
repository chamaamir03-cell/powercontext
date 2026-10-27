"""Archive only allowlisted, credential-free results from the local demonstration."""
from __future__ import annotations

import json
import hashlib
import platform
import subprocess
import uuid
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from bootstrap import APP, DEV, ROOT, VENDOR, read_env


def environment(env):
    names = ["langgenius/dify-api:1.18.0", "langgenius/dify-web:1.18.0",
             "langgenius/dify-sandbox:0.2.15", "langgenius/dify-plugin-daemon:0.6.10-local",
             "ob-npc-powercontext:1.4.1", "ob-npc-game:1.0.0",
             env.get("OLLAMA_IMAGE", "ollama/ollama:0.40.11")]
    images = json.loads(subprocess.check_output(["docker", "image", "inspect", *names], text=True))
    images = [{"image": name, "id": image["Id"], "architecture": image["Architecture"],
               "os": image["Os"], "size_bytes": image["Size"]} for name, image in zip(names, images)]
    opener = build_opener(ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{env['MODEL_PORT']}/api/tags", timeout=10) as response:
        models = json.load(response)["models"]
    model = next(item for item in models if item["name"] == env["MODEL_NAME"])
    with opener.open(f"http://127.0.0.1:{env['MODEL_PORT']}/api/version", timeout=10) as response:
        ollama = json.load(response)
    version = subprocess.check_output(["docker", "version", "--format", "{{.Server.Version}}"], text=True).strip()
    compose = subprocess.check_output(["docker", "compose", "version", "--short"], text=True).strip()
    files = ["backend/app.py", "backend/requirements.txt", "dsl/npc-chatflow.yml", "compose.yaml"]
    return {"host": platform.platform(), "host_python": platform.python_version(),
            "docker_engine": version, "docker_compose": compose, "ollama": ollama,
            "model": {key: model[key] for key in ["name", "digest", "size", "details"]},
            "images": images, "source_file_sha256": {
                name: hashlib.sha256((APP / name).read_bytes()).hexdigest() for name in files},
            "cpu_only_build": env.get("OLLAMA_IMAGE", "").startswith("ob-npc-ollama-cpu:")}


def main():
    runtime = APP / ".runtime"
    report = json.loads((runtime / "verification.json").read_text())
    for key in ["cross_conversation_recall", "hidden_ending", "player_isolation",
                "memory_outage", "restart_persistence", "chat_after_restart"]:
        assert report[key]["passed"], f"Incomplete verification: {key}"
    ids = [step["message_id"] for step in report["steps"]] + [report["chat_after_restart"]["message_id"]]
    ids = [str(uuid.UUID(identifier)) for identifier in ids]
    quoted = ",".join("'" + identifier + "'" for identifier in ids)
    query = f"""SELECT coalesce(json_agg(row_to_json(n)), '[]'::json) FROM (
      SELECT workflow_run_id, node_id, node_type, title, status, elapsed_time, outputs
      FROM workflow_node_executions
      WHERE workflow_run_id IN (SELECT workflow_run_id FROM messages WHERE id IN ({quoted}))
      ORDER BY created_at, node_id
    ) n"""
    result = subprocess.run([
        "docker", "compose", "-p", "ob-dify-npc", "--env-file", str(VENDOR / ".env"),
        "-f", str(VENDOR / "docker-compose.yaml"), "-f", str(DEV / "dify-overlay.yaml"),
        "exec", "-T", "db_postgres", "psql", "-U", "postgres", "-d", "dify", "-At", "-c", query,
    ], cwd=ROOT, text=True, capture_output=True, check=True, stdin=subprocess.DEVNULL)
    traces = json.loads(result.stdout)
    assert len(traces) == len(ids) * 9 and all(row["status"] == "succeeded" for row in traces)
    for row in traces:
        outputs = json.loads(row.pop("outputs") or "{}")
        if row["node_id"] in ["prepare", "commit"]:
            body = json.loads(outputs["body"])
            allowed = ["scope_id", "memory_count", "citations", "turn", "chapter", "state", "memory_ref"]
            row["result"] = {key: body[key] for key in allowed if key in body}
        elif row["node_id"] == "model_body":
            body = json.loads(outputs["body"])
            row["model"] = body["model"]
            context = json.loads(outputs["memory_context"])
            assert context["本轮回应要点"] in body["messages"][0]["content"]
            row["context_from_powercontext"] = context
            row["player_input"] = outputs["player_query"]
            row["model_user_message"] = body["messages"][-1]["content"]
        elif row["node_id"] == "model":
            body = json.loads(outputs["body"])
            row["model_result"] = {key: body[key] for key in ["model", "done", "eval_count", "prompt_eval_count"] if key in body}
    env = read_env(APP / ".env")
    payloads = {"dify-verification.json": report, "dify-node-executions.json": traces,
                "environment.json": environment(env)}
    private = json.loads((runtime / "app.json").read_text())
    secrets = [env[key] for key in ["GAME_TOKEN", "POWERCONTEXT_TOKEN", "DIFY_ADMIN_PASSWORD"]]
    secrets.append(private["api_key"])
    for name, content in payloads.items():
        encoded = json.dumps(content, ensure_ascii=False, indent=2) + "\n"
        assert not any(secret and secret in encoded for secret in secrets), "Refusing to archive a credential"
        (APP / "evidence" / name).write_text(encoded, encoding="utf-8")
    transcript = ["# 实际 Dify 对话记录", "", "以下台词来自真实本地模型；时间和会话 ID 见 JSON，未使用固定台词替代。", ""]
    for index, step in enumerate(report["steps"], 1):
        transcript += [f"## {index}. {step['input']}", "",
                       f"会话：`{step['conversation_id']}`；耗时：{step['elapsed_seconds']} 秒。", "", step["answer"], ""]
    transcript += ["## 服务重启后，新会话继续查看旧账", "", report["chat_after_restart"]["answer"], ""]
    (APP / "evidence" / "transcript.md").write_text("\n".join(transcript), encoding="utf-8")
    print(f"Archived {len(ids)} real Dify runs and {len(traces)} successful node executions; no credentials included.")


if __name__ == "__main__":
    main()
