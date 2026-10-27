"""Maintain the submitted DSL using the official Dify 1.18.0 node shapes."""
from pathlib import Path
import uuid
import yaml

APP = Path(__file__).resolve().parents[1]


def node(identifier, kind, title, data, index):
    return {"id": identifier, "type": "custom", "width": 244, "height": 100,
            "position": {"x": 80 + 310 * index, "y": 280},
            "sourcePosition": "right", "targetPosition": "left",
            "data": {"type": kind, "title": title, "desc": "", "selected": False, **data}}


def code(identifier, title, source, variables, outputs, index):
    return node(identifier, "code", title, {
        "code_language": "python3", "code": source.strip(),
        "variables": [{"variable": name, "value_selector": selector} for name, selector in variables.items()],
        "outputs": {name: {"type": kind} for name, kind in outputs.items()},
    }, index)


def http(identifier, title, url, body, index, token=False, timeout=30, retry=True):
    headers = "Content-Type: application/json"
    if token:
        headers += "\nX-Game-Token: {{#env.GAME_TOKEN#}}"
    return node(identifier, "http-request", title, {
        "method": "post", "url": url, "authorization": {"type": "no-auth"},
        "headers": headers, "params": "", "ssl_verify": True,
        "body": {"type": "json", "data": [{"key": "", "type": "text", "value": body}]},
        "timeout": {"connect": 10, "read": timeout, "write": 30},
        "retry_config": {"enabled": retry, "max_retries": 1, "retry_interval": 1000},
    }, index)


def generate():
    nodes = [node("start", "start", "进店 · 存档暗号", {"variables": [{
        "variable": "player_code", "label": "存档暗号（4—48位英文字母、数字、_ 或 -，新会话保持相同）",
        "type": "text-input", "required": True, "max_length": 48, "options": [],
    }]}, 0)]
    nodes.append(code("prepare_body", "整理本轮输入", '''
import json
import re
import uuid

def main(player_code: str, query: str, session_id: str) -> dict:
    player_code = player_code.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,48}", player_code):
        raise ValueError("存档暗号应为4—48位字母、数字、下划线或连字符。")
    if not query.strip() or len(query) > 500:
        raise ValueError("请输入1—500字的动作或台词。")
    turn_id = uuid.uuid4().hex
    payload = {"player_code": player_code, "query": query,
               "session_id": session_id, "turn_id": turn_id}
    return {"body": json.dumps(payload, ensure_ascii=False),
            "turn_id": turn_id, "player_code": player_code}
''', {"player_code": ["start", "player_code"], "query": ["sys", "query"],
      "session_id": ["sys", "conversation_id"]},
      {"body": "string", "turn_id": "string", "player_code": "string"}, 1))
    nodes.append(http("prepare", "PowerContext · 读旧账", "{{#env.GAME_API_URL#}}/turns/prepare",
                      "{{#prepare_body.body#}}", 2, token=True))
    nodes.append(code("model_body", "把真实旧账交给掌柜", '''
import json

def main(body: str, status_code: int, model_name: str) -> dict:
    if status_code != 200:
        raise ValueError("掌柜的账本暂时打不开，本轮尚未存档，请稍后再试。")
    saved = json.loads(body)
    context = json.loads(saved["model_context"])
    history = "；".join(context["旧账事实"]) or "没有可翻的关键旧账。"
    facts = "；".join(key + "：" + value for key, value in context["现在的事实（优先于历史）"].items())
    ledger = ("\\n\\n来自本局账本的可靠事实：\\n现在：" + facts +
              "\\n历史（只说明当时）：" + history +
              "\\n本轮已发生的事情及回应要求：" + context["本轮回应要点"])
    player_message = saved["player_query"]
    if saved.get("model_user_hint"):
        player_message += "\\n" + saved["model_user_hint"]
    payload = {"model": model_name, "stream": False, "keep_alive": "10m",
               "messages": [{"role": "system", "content": saved["system_prompt"] + ledger},
                            {"role": "user", "content": player_message}],
               "options": {"temperature": 0.4, "top_p": 0.85, "num_ctx": 4096,
                           "num_predict": 240, "seed": 42,
                           "stop": ["\\n玩家", "\\n\\n玩家", "\\n用户", "\\n\\n用户"]}}
    if model_name.startswith("qwen3"):
        payload["think"] = False
    return {"body": json.dumps(payload, ensure_ascii=False),
            "scope_id": saved["scope_id"], "recalled_memories": saved["memory_count"],
            "memory_context": saved["model_context"], "player_query": saved["player_query"]}
''', {"body": ["prepare", "body"], "status_code": ["prepare", "status_code"],
      "model_name": ["env", "MODEL_NAME"]},
      {"body": "string", "scope_id": "string", "recalled_memories": "number", "memory_context": "string", "player_query": "string"}, 3))
    nodes.append(http("model", "沈不服 · 现编台词", "{{#env.OLLAMA_URL#}}/api/chat",
                      "{{#model_body.body#}}", 4, timeout=240, retry=False))
    nodes.append(code("commit_body", "准备存档 · 模型不改规则", '''
import json
import re

def main(body: str, status_code: int, player_code: str, turn_id: str) -> dict:
    if status_code != 200:
        raise ValueError("掌柜暂时组织不好语言，本轮尚未存档，请稍后再试。")
    reply = json.loads(body).get("message", {}).get("content", "").strip()
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.S).strip()
    if not reply:
        raise ValueError("掌柜一时没想好怎么接话，本轮尚未存档，请稍后再试。")
    payload = {"player_code": player_code, "turn_id": turn_id, "reply": reply[:900]}
    return {"body": json.dumps(payload, ensure_ascii=False)}
''', {"body": ["model", "body"], "status_code": ["model", "status_code"],
      "player_code": ["prepare_body", "player_code"], "turn_id": ["prepare_body", "turn_id"]},
      {"body": "string"}, 5))
    nodes.append(http("commit", "PowerContext · 写下这一笔", "{{#env.GAME_API_URL#}}/turns/commit",
                      "{{#commit_body.body#}}", 6, token=True))
    nodes.append(code("format", "只展示已确认存档", '''
import json

def main(body: str, status_code: int) -> dict:
    if status_code != 200:
        raise ValueError("存档尚未确认，本轮不能显示保存成功，请重新发送输入。")
    saved = json.loads(body)
    return {"answer": saved["reply"] + saved["footer"], "turn": saved["turn"],
            "scope_id": saved["scope_id"],
            "memory_ref": json.dumps(saved["memory_ref"], ensure_ascii=False)}
''', {"body": ["commit", "body"], "status_code": ["commit", "status_code"]},
      {"answer": "string", "turn": "number", "scope_id": "string", "memory_ref": "string"}, 7))
    nodes.append(node("answer", "answer", "掌柜开口了", {"answer": "{{#format.answer#}}", "variables": []}, 8))
    edges = [{"id": f"{left['id']}-{right['id']}", "type": "custom", "source": left["id"],
              "sourceHandle": "source", "target": right["id"], "targetHandle": "target",
              "data": {"sourceType": left["data"]["type"], "targetType": right["data"]["type"],
                       "isInIteration": False, "isInLoop": False}}
             for left, right in zip(nodes, nodes[1:])]
    environment = []
    for name, value, kind, description in [
        ("GAME_API_URL", "http://npc-game:8090", "string", "账本服务内部地址"),
        ("GAME_TOKEN", "", "secret", "填入本地dify/.env中的GAME_TOKEN"),
        ("OLLAMA_URL", "http://npc-model:11434", "string", "模型服务内部地址"),
        ("MODEL_NAME", "qwen3:1.7b", "string", "已下载的本地模型"),
    ]:
        environment.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, "ob-npc/" + name)),
                            "name": name, "value": value, "value_type": kind,
                            "selector": ["env", name], "description": description})
    result = {
        "app": {"name": "嘴硬掌柜：这笔账我记下了", "description": "在雨夜小店惹事、赔礼、修门；用同一个存档暗号开始新会话，掌柜照样记得旧账。试着把嘴硬的掌柜哄成自己人，解锁雨夜小店的隐藏结局。",
                "icon": "☔", "icon_background": "#FFF0D6", "mode": "advanced-chat",
                "use_icon_as_answer_icon": True},
        "dependencies": [], "kind": "app", "version": "0.3.1",
        "workflow": {"conversation_variables": [], "environment_variables": environment,
                     "features": {"file_upload": {"enabled": False},
                         "opening_statement": "雨停之前，沈不服的小卖部都不打烊。先填一个存档暗号，再说‘开始’。想改变物品或关系，请发送动作原文：偷拿蓝伞、归还蓝伞、道歉、送奶茶、帮忙修门。其他输入用于自由聊天。换个会话、用同一暗号回来，掌柜仍会翻旧账；输入 /重开 可开启新章。",
                         "suggested_questions": ["开始", "偷拿蓝伞", "送奶茶", "查看旧账"],
                         "suggested_questions_after_answer": {"enabled": False},
                         "retriever_resource": {"enabled": False},
                         "sensitive_word_avoidance": {"enabled": False},
                         "speech_to_text": {"enabled": False},
                         "text_to_speech": {"enabled": False, "language": "", "voice": ""}},
                     "graph": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.55}}},
    }
    destination = APP / "dsl" / "npc-chatflow.yml"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(yaml.safe_dump(result, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
    print(f"Generated {destination} ({len(nodes)} nodes; no model plugins required)")


if __name__ == "__main__":
    generate()
