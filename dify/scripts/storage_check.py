"""Check the Docker host's backing disk, including WSL's Windows VHD drive."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def check_storage(minimum_gib=20):
    distro = os.environ.get("WSL_DISTRO_NAME")
    if distro and shutil.which("powershell.exe"):
        # Query all registrations, then match locally; do not interpolate a name into shell code.
        script = r"""
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $OutputEncoding
$taskRows = @(Get-ChildItem -LiteralPath HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss | ForEach-Object {
  $taskDistro = Get-ItemProperty -LiteralPath $_.PSPath
  $taskRoot = [System.IO.Path]::GetPathRoot($taskDistro.BasePath)
  $taskDrive = [System.IO.DriveInfo]::new($taskRoot)
  [PSCustomObject]@{name=$taskDistro.DistributionName; drive=$taskRoot; free=$taskDrive.AvailableFreeSpace}
})
ConvertTo-Json -InputObject $taskRows -Compress
"""
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, check=True, stdin=subprocess.DEVNULL, timeout=30)
        rows = json.loads(result.stdout.decode("utf-8-sig"))
        entry = next(row for row in rows if row["name"] == distro)
        free_gib = entry["free"] / 1024 ** 3
        location = "WSL backing drive " + entry["drive"]
    else:
        result = subprocess.run(["docker", "info", "--format", "{{.DockerRootDir}}"],
                                capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL)
        path = Path(result.stdout.strip())
        while not path.exists() and path != path.parent:
            path = path.parent
        free_gib = shutil.disk_usage(path).free / 1024 ** 3
        location = "Docker data filesystem"
    print(f"Storage: {location}, {free_gib:.1f} GiB free; required margin {minimum_gib} GiB.", flush=True)
    if free_gib < minimum_gib:
        raise RuntimeError("Insufficient Docker host disk space. Free space or relocate the WSL/Docker data disk before continuing.")


if __name__ == "__main__":
    check_storage()
