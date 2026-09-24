"""Read-only detection of the external tools and phones OATH depends on."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

REQUIRED_TOOLS = ("adb", "idevicebackup2", "idevice_id")


@dataclass(frozen=True)
class ToolStatus:
    name: str
    path: str | None

    @property
    def found(self) -> bool:
        return self.path is not None


def check_tools(names: tuple[str, ...] = REQUIRED_TOOLS) -> list[ToolStatus]:
    return [ToolStatus(name, shutil.which(name)) for name in names]


def _run(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.returncode == 0 else ""


def parse_adb_devices(output: str) -> list[str]:
    """Serials of authorised devices from `adb devices` output."""
    serials = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def android_devices() -> list[str]:
    if shutil.which("adb") is None:
        return []
    return parse_adb_devices(_run(["adb", "devices"]))


def ios_devices() -> list[str]:
    if shutil.which("idevice_id") is None:
        return []
    return [line.strip() for line in _run(["idevice_id", "-l"]).splitlines() if line.strip()]
