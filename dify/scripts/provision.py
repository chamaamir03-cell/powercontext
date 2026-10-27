"""Initialize the isolated Dify instance, import the delivered DSL, and publish it."""
from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path
from urllib.parse import unquote

import httpx
import yaml

APP = Path(__file__).resolve().parents[1]


def read_env(path: Path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


class Console:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.client = httpx.Client(base_url=self.base, timeout=60, trust_env=False,
                                   headers={"Origin": self.base, "Referer": self.base + "/apps"})

    def request(self, method, path, payload=None):
        csrf = next((cookie.value for cookie in self.client.cookies.jar
                     if cookie.name.endswith("csrf_token")), None)
        headers = {"X-CSRF-Token": unquote(csrf)} if csrf else {}
        response = self.client.request(method, "/console/api" + path, json=payload, headers=headers)
        if response.is_error:
            try:
                detail = response.json()
                code = detail.get("code", detail.get("error", "unknown"))
                message = str(detail.get("message", detail.get("description", "")))[:350]
            except ValueError:
                code, message = "non-json-response", ""
            raise RuntimeError(f"Dify {path}: HTTP {response.status_code}, {code}: {message}")
        return response.json() if response.content else {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://npc-dify")
    parser.add_argument("--env-file", type=Path, default=Path("/run/npc.env"))
    parser.add_argument("--output", type=Path, default=APP / "runtime" / "app.json")
    args = parser.parse_args()
    env = read_env(args.env_file)
    console = Console(args.base_url)
    for attempt in range(90):
        try:
            setup = console.request("GET", "/setup")
            break
        except (httpx.HTTPError, RuntimeError):
            if attempt == 89:
                raise
            time.sleep(5)
    if setup["step"] == "not_started":
        console.request("POST", "/setup", {"email": env["DIFY_ADMIN_EMAIL"], "name": "雨夜小店管理员",
                                            "password": env["DIFY_ADMIN_PASSWORD"], "language": "zh-Hans"})
    console.request("POST", "/login", {"email": env["DIFY_ADMIN_EMAIL"],
                                       "password": base64.b64encode(env["DIFY_ADMIN_PASSWORD"].encode()).decode(),
                                       "language": "zh-Hans", "remember_me": True})
    definition = yaml.safe_load((APP / "dsl" / "npc-chatflow.yml").read_text(encoding="utf-8"))
    values = {"GAME_TOKEN": env["GAME_TOKEN"], "MODEL_NAME": env["MODEL_NAME"]}
    for variable in definition["workflow"]["environment_variables"]:
        if variable["name"] in values:
            variable["value"] = values[variable["name"]]
    payload = {"mode": "yaml-content", "yaml_content": yaml.safe_dump(definition, allow_unicode=True),
               "name": definition["app"]["name"]}
    if args.output.exists():
        previous = json.loads(args.output.read_text())
        if previous.get("console_base_url") == args.base_url:
            payload["app_id"] = previous["app_id"]
    imported = console.request("POST", "/apps/imports", payload)
    if imported.get("status") == "pending":
        imported = console.request("POST", f"/apps/imports/{imported['id']}/confirm", {})
    app_id = imported.get("app_id")
    if not app_id or imported.get("status") == "failed":
        raise RuntimeError(f"Dify did not finish importing the DSL: {imported.get('status')}")
    console.request("POST", f"/apps/{app_id}/workflows/publish", {"marked_name": "比赛验收版", "marked_comment": "PowerContext持久化账本与本地模型实测"})
    console.request("POST", f"/apps/{app_id}/api-enable", {"enable_api": True})
    console.request("POST", f"/apps/{app_id}/site-enable", {"enable_site": True})
    keys = console.request("GET", f"/apps/{app_id}/api-keys").get("data", [])
    key = next((item.get("token") for item in keys if item.get("token")), None)
    if not key:
        key = console.request("POST", f"/apps/{app_id}/api-keys", {}).get("token")
    if not key:
        raise RuntimeError("Dify did not return an application API key.")
    app = console.request("GET", f"/apps/{app_id}")
    site = app.get("site") or {}
    site_code = site.get("access_token") or site.get("code")
    public_base = "http://localhost:" + env["DIFY_PORT"]
    result = {"app_id": app_id, "api_key": key, "console_base_url": args.base_url,
              "public_base_url": public_base, "chat_url": public_base + "/chat/" + site_code if site_code else "",
              "dsl_version": imported.get("current_dsl_version"), "dify_version": "1.18.0"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.chmod(0o600)
    print(json.dumps({"imported": True, "published": True, "app_id": app_id,
                      "chat_url": result["chat_url"], "credentials_file": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
