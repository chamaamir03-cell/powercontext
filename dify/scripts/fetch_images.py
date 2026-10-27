"""Optional proxy-friendly public image download; leaves daemon configuration alone."""
from __future__ import annotations

import hashlib
import argparse
import json
import os
import subprocess
import tarfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / ".dify-dev"
CRANE_VERSION = "v0.22.1"
IMAGES = [
    "python:3.12-slim", "ollama/ollama:0.40.11", "langgenius/dify-api:1.18.0",
    "langgenius/dify-web:1.18.0", "langgenius/dify-sandbox:0.2.15",
    "langgenius/dify-plugin-daemon:0.6.10-local", "postgres:15-alpine",
    "redis:6-alpine", "busybox:latest", "ubuntu/squid:latest", "nginx:latest",
]


def download(url: str, destination: Path):
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={"User-Agent": "ob-npc-image-fetch/1.0"}), timeout=120) as response:
                with destination.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            return
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def metadata_for(url: str) -> dict:
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={"User-Agent": "ob-npc-image-fetch/1.0"}), timeout=30) as response:
                return json.load(response)
        except (OSError, ValueError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="+", choices=IMAGES)
    parser.add_argument("--skip-model", action="store_true", help="Skip the full Ollama image when building the optional CPU runtime")
    parser.add_argument("--cache", default="images", choices=["images", "runtime-images", "support-images"])
    args = parser.parse_args()
    from storage_check import check_storage
    check_storage(20)
    binary = DEV / "bin" / "crane"
    binary.parent.mkdir(parents=True, exist_ok=True)
    if not binary.exists():
        name = "go-containerregistry_Linux_x86_64.tar.gz"
        base = f"https://github.com/google/go-containerregistry/releases/download/{CRANE_VERSION}"
        archive, checksums = binary.parent / name, binary.parent / "checksums.txt"
        download(f"{base}/{name}", archive)
        download(f"{base}/checksums.txt", checksums)
        expected = next(line.split()[0] for line in checksums.read_text().splitlines() if line.endswith(name))
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Official crane archive checksum mismatch")
        with tarfile.open(archive, "r:gz") as bundle:
            member = bundle.getmember("crane")
            with bundle.extractfile(member) as source:
                binary.write_bytes(source.read())
        binary.chmod(0o755)
    archives = DEV / args.cache
    archives.mkdir(exist_ok=True)
    env = os.environ.copy()
    # This tool downloads public images; an obsolete desktop helper is unnecessary.
    docker_config = DEV / "docker-config"
    docker_config.mkdir(exist_ok=True)
    if not (docker_config / "config.json").exists():
        config = {"auths": {}}
        discovered = subprocess.run(["docker", "info", "--format", "{{json .ClientInfo.Plugins}}"],
                                    capture_output=True, text=True)
        if discovered.returncode == 0:
            plugins = json.loads(discovered.stdout or "[]")
            config["cliPluginsExtraDirs"] = sorted({str(Path(item["Path"]).parent)
                                                    for item in plugins if item.get("Path")})
        (docker_config / "config.json").write_text(json.dumps(config))
    env["DOCKER_CONFIG"] = str(docker_config)
    receipt = "image-lock.json" if args.cache == "images" else f"image-lock-{args.cache.removesuffix('-images')}.json"
    receipt_path = DEV / receipt
    records = {item["image"]: item for item in json.loads(receipt_path.read_text())} if receipt_path.exists() else {}
    for image in args.only or IMAGES:
        if args.skip_model and image.startswith("ollama/"):
            continue
        print(f"Checking {image}", flush=True)
        exists = subprocess.run(["docker", "image", "inspect", image], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        repository, tag = image.rsplit(":", 1)
        repository = repository if "/" in repository else "library/" + repository
        digest = records.get(image, {}).get("manifest_digest")
        provenance = "existing local image"
        if not exists:
            archive = archives / (image.replace("/", "--").replace(":", "_") + ".tar")
            sidecar = archive.with_suffix(".metadata.json")
            if not archive.exists():
                metadata_url = f"https://hub.docker.com/v2/repositories/{repository}/tags/{tag}"
                metadata = metadata_for(metadata_url)
                digest = next(item["digest"] for item in metadata["images"]
                              if item.get("os") == "linux" and item.get("architecture") == "amd64")
                partial = archive.with_suffix(".partial")
                print(f"Downloading {image} through the command's existing proxy settings.", flush=True)
                sources = [f"mirror.gcr.io/{repository}@{digest}",
                           f"docker.m.daocloud.io/{repository}@{digest}",
                           f"registry-1.docker.io/{repository}@{digest}"]
                for source in sources:
                    # Fetch exactly the manifest verified against the official registry.
                    result = subprocess.run([str(binary), "pull", "--platform", "linux/amd64", source, str(partial)], env=env)
                    if result.returncode == 0:
                        break
                else:
                    raise RuntimeError(f"Unable to download verified public image {image}")
                partial.replace(archive)
                sidecar.write_text(json.dumps({"image": image, "manifest_digest": digest}))
                provenance = "downloaded exact official manifest"
            else:
                provenance = "previously downloaded local archive"
                if sidecar.exists():
                    cached = json.loads(sidecar.read_text())
                    assert cached["image"] == image, "Cached archive metadata has a different image name"
                    digest = cached["manifest_digest"]
                print(f"Importing cached archive for {image}; no registry lookup needed.", flush=True)
            subprocess.run(["docker", "load", "-i", str(archive)], env=env, check=True)
            with tarfile.open(archive, "r:") as bundle:
                manifest = json.load(bundle.extractfile("manifest.json"))[0]
                configuration = bundle.extractfile(manifest["Config"]).read()
                local_id = "sha256:" + hashlib.sha256(configuration).hexdigest()
            subprocess.run(["docker", "tag", local_id, image], env=env, check=True)
        image_id = subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                                           env=env, text=True).strip()
        old = records.get(image, {})
        if exists and old.get("local_image_id") != image_id:
            digest = None  # Do not attribute an unverified local tag to a registry digest.
        records[image] = {"image": image, "platform": "linux/amd64", "manifest_digest": digest,
                          "local_image_id": image_id, "source": provenance}
        receipt_path.write_text(json.dumps(list(records.values()), indent=2) + "\n")
        print(f"Ready: {image}", flush=True)


if __name__ == "__main__":
    main()
