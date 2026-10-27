"""Optional CPU-only Ollama runtime, built from the pinned official sources.

Requires Linux x86_64, Python 3.12, CMake >=3.24, g++, make, git and patch.
This keeps the official Ollama Go executable and builds its documented CPU runner.
"""
from __future__ import annotations

import hashlib
import platform
import shutil
import tarfile
from pathlib import Path

from bootstrap import APP, DEV, prepare, read_env, run, write_env
from fetch_images import download


VERSION = "0.40.11"
LLAMA_VERSION = "b11845"
SOURCES = [
    ("source.tar.gz", f"https://codeload.github.com/ollama/ollama/tar.gz/refs/tags/v{VERSION}",
     "9733d6230d8c8a75ccdf368c53132d90ee49009ce677776d897e96b50cc1c654"),
    (f"llama-{LLAMA_VERSION}.tar.gz", f"https://codeload.github.com/ggml-org/llama.cpp/tar.gz/refs/tags/{LLAMA_VERSION}",
     "ed2274de0cc300273617339bc6b4bc9d8efbdd3ef9953563205e53ac0c70fdb5"),
]
GO_LAYER = "9fa16ea649112cf20f6068cdd3714384447644b1117ee575450f9196508c6dcb"


def verified(path: Path, url: str, expected: str):
    if not path.exists():
        download(url, path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError(f"Checksum mismatch: {path.name}; remove that cached file and retry")


def main():
    assert platform.system() == "Linux" and platform.machine() == "x86_64", "This optional build targets Linux x86_64"
    for tool in ["cmake", "g++", "make", "git", "patch"]:
        assert shutil.which(tool), f"Install the build prerequisite: {tool}"
    from storage_check import check_storage
    check_storage(20)
    prepare()
    work = DEV / "ollama-cpu"
    work.mkdir(exist_ok=True)
    for name, url, expected in SOURCES:
        archive = work / name
        verified(archive, url, expected)
        marker = work / (name + ".extracted")
        if not marker.exists():
            with tarfile.open(archive) as bundle:
                bundle.extractall(work, filter="data")
            marker.touch()
    source = work / f"ollama-{VERSION}"
    llama = work / f"llama.cpp-{LLAMA_VERSION}"
    assert (source / "LLAMA_CPP_VERSION").read_text().strip() == LLAMA_VERSION
    run("cmake", "-S", source / "llama/server", "--preset", "cpu",
        f"-DFETCHCONTENT_SOURCE_DIR_LLAMA_CPP={llama}", "-DGGML_CPU_ALL_VARIANTS=OFF")
    build = source / "build/llama-server-cpu"
    run("cmake", "--build", build, "--target", "llama-server", "llama-quantize", "--parallel", "2")
    run("cmake", "--install", build, "--component", "llama-server", "--strip")
    context = work / "image"
    context.mkdir(exist_ok=True)
    shutil.copytree(source / "dist/lib/ollama", context / "runtime", dirs_exist_ok=True)
    layer = work / "go-layer.tar.gz"
    verified(layer, f"https://mirror.gcr.io/v2/ollama/ollama/blobs/sha256:{GO_LAYER}", GO_LAYER)
    with tarfile.open(layer) as bundle:
        member = next(item for item in bundle if item.isfile() and item.name.endswith("/ollama"))
        (context / "ollama").write_bytes(bundle.extractfile(member).read())
    (context / "ollama").chmod(0o755)
    (context / "Dockerfile").write_text("""FROM python:3.12-slim
ARG HTTP_PROXY
ARG HTTPS_PROXY
RUN apt-get -o Acquire::http::Proxy=\"$HTTP_PROXY\" update && apt-get -o Acquire::http::Proxy=\"$HTTP_PROXY\" install -y --no-install-recommends libstdc++6 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY ollama /usr/local/bin/ollama
COPY runtime /usr/local/lib/ollama
ENV OLLAMA_HOST=0.0.0.0:11434
EXPOSE 11434
ENTRYPOINT [\"/usr/local/bin/ollama\"]
CMD [\"serve\"]
""", encoding="utf-8")
    image = f"ob-npc-ollama-cpu:{VERSION}"
    run("docker", "build", "--network", "host", "--build-arg", "HTTP_PROXY",
        "--build-arg", "HTTPS_PROXY", "-t", image, context)
    env = read_env(APP / ".env")
    env["OLLAMA_IMAGE"] = image
    write_env(APP / ".env", env)
    print("Built the CPU-only runtime and selected it in dify/.env. Continue with bootstrap.py up.")


if __name__ == "__main__":
    main()
