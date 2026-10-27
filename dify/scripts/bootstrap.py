"""Prepare an isolated local Dify 1.18.0 + PowerContext + Ollama demonstration."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import tarfile
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "dify"
DEV = ROOT / ".dify-dev"
VENDOR = DEV / "vendor" / "dify-1.18.0" / "docker"
VERSION = "1.18.0"


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


def write_env(path: Path, values: dict[str, str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    path.chmod(0o600)


def run(*args: str, capture=False):
    print("Running:", " ".join(map(str, args)), flush=True)
    env = os.environ.copy()
    env.setdefault("BUILD_HTTP_PROXY", env.get("HTTP_PROXY", ""))
    env.setdefault("BUILD_HTTPS_PROXY", env.get("HTTPS_PROXY", ""))
    env.setdefault("DOCKER_BUILDKIT", "0")
    return subprocess.run(list(map(str, args)), cwd=ROOT, check=True,
                          text=True, capture_output=capture, env=env, stdin=subprocess.DEVNULL)


def game_compose(*args: str):
    return run("docker", "compose", "--env-file", APP / ".env", "-f", APP / "compose.yaml", *args)


def dify_compose(*args: str):
    return run("docker", "compose", "-p", "ob-dify-npc", "--env-file", VENDOR / ".env",
               "-f", VENDOR / "docker-compose.yaml", "-f", DEV / "dify-overlay.yaml", *args)


def prepare():
    DEV.mkdir(exist_ok=True)
    (DEV / "logs").mkdir(exist_ok=True)
    if not (VENDOR / "docker-compose.yaml").exists():
        archive = DEV / f"dify-{VERSION}.tar.gz"
        url = f"https://codeload.github.com/langgenius/dify/tar.gz/refs/tags/{VERSION}"
        print("Downloading the pinned official Dify deployment assets.", flush=True)
        with urlopen(Request(url, headers={"User-Agent": "ob-npc-bootstrap/1.0"}), timeout=120) as response:
            with archive.open("wb") as target:
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
        destination = (DEV / "vendor").resolve()
        destination.mkdir(exist_ok=True)
        with tarfile.open(archive, "r:gz") as bundle:
            members = [member for member in bundle.getmembers()
                       if "/docker/" in member.name or member.name.endswith("/docker")]
            for member in members:
                if not (destination / member.name).resolve().is_relative_to(destination):
                    raise RuntimeError("Invalid upstream archive member path")
            bundle.extractall(destination, members=members, filter="data")
    local = read_env(APP / ".env")
    for key in ["GAME_TOKEN", "POWERCONTEXT_TOKEN"]:
        if not local.get(key) or local[key].startswith("replace-with-"):
            local[key] = secrets.token_urlsafe(24)
    local.setdefault("DIFY_ADMIN_PASSWORD", "Npc9_" + secrets.token_urlsafe(24))
    for key, value in {"GAME_PORT": "18082", "POWERCONTEXT_PORT": "18081", "MODEL_PORT": "11435",
                       "DIFY_PORT": "18090", "MODEL_NAME": "qwen3:1.7b",
                       "DIFY_ADMIN_EMAIL": "npc-demo@example.com"}.items():
        local.setdefault(key, value)
    write_env(APP / ".env", local)
    config = read_env(VENDOR / ".env") or read_env(VENDOR / ".env.example")
    for key in ["SECRET_KEY", "DB_PASSWORD", "REDIS_PASSWORD", "PLUGIN_DAEMON_KEY",
                "PLUGIN_DIFY_INNER_API_KEY", "CODE_EXECUTION_API_KEY"]:
        if not (VENDOR / ".env").exists():
            config[key] = secrets.token_urlsafe(32)
    config.update({
        "COMPOSE_PROFILES": "postgresql", "DB_TYPE": "postgresql", "VECTOR_STORE": "weaviate",
        "EXPOSE_NGINX_PORT": local["DIFY_PORT"], "EXPOSE_NGINX_SSL_PORT": "18443",
        "EXPOSE_PLUGIN_DEBUGGING_PORT": "15003", "SERVER_WORKER_AMOUNT": "1",
        "CELERY_WORKER_AMOUNT": "1", "CELERY_AUTO_SCALE": "false",
        "SANDBOX_API_KEY": config["CODE_EXECUTION_API_KEY"],
        "CELERY_BROKER_URL": f"redis://:{config['REDIS_PASSWORD']}@redis:6379/1",
        "SSRF_DEFAULT_TIME_OUT": "300", "SSRF_DEFAULT_CONNECT_TIME_OUT": "15",
        "SSRF_DEFAULT_READ_TIME_OUT": "300", "SSRF_DEFAULT_WRITE_TIME_OUT": "30",
    })
    write_env(VENDOR / ".env", config)
    overlay = f"""services:
  init_permissions:
    volumes: !override
      - npc_storage:/app/api/storage
  api:
    networks: [ssrf_proxy_network, default, npc_net]
    depends_on: !override
      init_permissions:
        condition: service_completed_successfully
      db_postgres:
        condition: service_healthy
      redis:
        condition: service_started
    volumes: !override
      - npc_storage:/app/api/storage
  worker:
    networks: [ssrf_proxy_network, default, npc_net]
    depends_on: !override
      init_permissions:
        condition: service_completed_successfully
      db_postgres:
        condition: service_healthy
      redis:
        condition: service_started
    volumes: !override
      - npc_storage:/app/api/storage
  db_postgres:
    volumes: !override
      - npc_postgres:/var/lib/postgresql/data
  plugin_daemon:
    volumes: !override
      - npc_plugins:/app/storage
    ports: !override
      - '127.0.0.1:15003:5003'
  ssrf_proxy:
    networks: [ssrf_proxy_network, default, npc_net]
  nginx:
    networks:
      default:
      npc_net:
        aliases: [npc-dify]
    entrypoint: [nginx]
    command: [-g, 'daemon off;']
    volumes: !override
      - {APP / 'deploy/nginx.conf'}:/etc/nginx/nginx.conf:ro
    ports: !override
      - '127.0.0.1:{local['DIFY_PORT']}:80'
networks:
  npc_net:
    external: true
    name: ob-npc-net
volumes:
  npc_storage:
  npc_postgres:
  npc_plugins:
"""
    (DEV / "dify-overlay.yaml").write_text(overlay, encoding="utf-8")
    print("Prepared isolated runtime. Local credentials are in dify/.env (not tracked).", flush=True)


def up():
    from storage_check import check_storage
    check_storage(2 if (APP / ".runtime/app.json").exists() else 20)
    prepare()
    game_compose("up", "-d", "--build")
    networks = json.loads(run("docker", "network", "inspect", "ob-npc-net", capture=True).stdout)
    subnets = ",".join(item["Subnet"] for network in networks for item in network["IPAM"]["Config"])
    config = read_env(VENDOR / ".env")
    config["SSRF_PROXY_ALLOW_PRIVATE_IPS"] = subnets
    write_env(VENDOR / ".env", config)
    dify_compose("up", "-d", "api", "worker", "worker_beat", "web", "db_postgres", "redis",
                 "sandbox", "plugin_daemon", "ssrf_proxy", "nginx")
    # Refresh mounts and cached service addresses after container recreation.
    dify_compose("up", "-d", "--no-deps", "--force-recreate", "nginx")
    dify_compose("restart", "ssrf_proxy")
    pull_model()
    print("Services started. Run scripts/provision.py after Dify migrations complete.", flush=True)


def pull_model():
    settings = read_env(APP / ".env")
    model = settings["MODEL_NAME"]
    image = settings.get("OLLAMA_IMAGE", "ollama/ollama:0.40.11")
    present = subprocess.run(["docker", "compose", "--env-file", str(APP / ".env"),
                              "-f", str(APP / "compose.yaml"), "exec", "-T", "model", "ollama", "show", model],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    if present.returncode == 0:
        print("Configured model is already present in the persistent volume.", flush=True)
        return
    proxy = os.environ.get("HTTPS_PROXY", "")
    if urlsplit(proxy).hostname not in ["localhost", "127.0.0.1"]:
        game_compose("exec", "-T", "model", "ollama", "pull", model)
        return
    # A host-loopback proxy is not reachable as 127.0.0.1 from a bridge container.
    # Use a temporary host-network downloader with the same persistent model volume.
    game_compose("stop", "model")
    name = "ob-npc-model-download-" + uuid.uuid4().hex[:8]
    proxy_file = DEV / "model-download.env"
    write_env(proxy_file, {"HTTPS_PROXY": proxy, "NO_PROXY": "localhost,127.0.0.1",
                           "OLLAMA_HOST": "127.0.0.1:19134"})
    run("docker", "run", "-d", "--pull", "never", "--name", name, "--network", "host",
        "--env-file", proxy_file, "-v", "ob-npc_model_data:/root/.ollama", image, "serve")
    try:
        for attempt in range(30):
            ready = subprocess.run(["docker", "exec", name, "ollama", "list"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Temporary local model downloader did not become ready")
        run("docker", "exec", name, "ollama", "pull", model)
    finally:
        run("docker", "rm", "-f", name)
        game_compose("up", "-d", "--no-build", "model")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "up", "status", "stop"])
    command = parser.parse_args().command
    if command == "prepare":
        prepare()
    elif command == "up":
        up()
    elif command == "status":
        game_compose("ps")
        dify_compose("ps")
    else:
        # Keep named volumes and the PowerContext ledger for the next session.
        dify_compose("stop")
        game_compose("stop")


if __name__ == "__main__":
    main()
