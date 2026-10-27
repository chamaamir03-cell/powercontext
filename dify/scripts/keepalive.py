"""Keep a foreground WSL session while this demonstration's containers run."""
import subprocess
import time


def main():
    started = time.monotonic()
    last_active = None
    last_status = None
    print("WSL keeper started; watching only ob-npc and ob-dify-npc.", flush=True)
    while True:
        names = []
        known = True
        for project in ["ob-npc", "ob-dify-npc"]:
            try:
                result = subprocess.run([
                    "docker", "ps", "--filter", "label=com.docker.compose.project=" + project,
                    "--format", "{{.Names}}",
                ], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
                known = known and result.returncode == 0
                names.extend(result.stdout.splitlines())
            except (OSError, subprocess.TimeoutExpired):
                known = False
        now = time.monotonic()
        if names:
            last_active = now
        status = (known, len(names))
        if status != last_status:
            print(f"Docker reachable: {known}; active demo containers: {len(names)}", flush=True)
            last_status = status
        if known and not names and last_active is not None and now - last_active > 30:
            print("Demo containers stopped; WSL keeper exiting.", flush=True)
            return
        if last_active is None and now - started > 300:
            print("No demo containers started within five minutes; exiting.", flush=True)
            return
        time.sleep(10)


if __name__ == "__main__":
    main()
