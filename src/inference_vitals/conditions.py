"""What the Mac was doing when a run happened, so a slow page can be told apart from a hot or busy Mac.

Read from macOS's own commands (pmset, sysctl, sw_vers, ps), none of which need sudo. Process names are
kept, never their arguments, since arguments can hold file names.
"""

import re
import subprocess
import time

MODES = {"0": "normal", "1": "low power", "2": "high power"}


def _out(*cmd) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


def thermal(text: str) -> dict:
    def level(pattern, default):
        m = re.search(pattern, text, re.IGNORECASE)
        return int(m.group(1)) if m else default

    return {
        "thermal_warning": level(r"thermal warning level set to (\d+)", 0),
        "performance_warning": level(r"performance warning level set to (\d+)", 0),
        "cpu_speed_limit": level(r"CPU_Speed_Limit\s*=\s*(\d+)", 100),
    }


def on_mains(batt: str) -> bool:
    return "'AC Power'" in batt


def power_mode(pm: str) -> str:
    m = re.search(r"\bpowermode\s+(\d)", pm)
    return MODES.get(m.group(1), "unknown") if m else "unknown"


def busy_processes(ps: str, over: float = 20.0) -> list:
    busy = []
    for line in ps.splitlines():
        m = re.match(r"\s*([\d.]+)\s+(.+)$", line)
        if m and float(m.group(1)) >= over:
            busy.append([m.group(2).rstrip().rsplit("/", 1)[-1], float(m.group(1))])
    return busy


def snapshot() -> dict:
    limit = _out("sysctl", "-n", "iogpu.wired_limit_mb").strip()
    return {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "macos": _out("sw_vers", "-productVersion").strip(),
        "model": _out("sysctl", "-n", "hw.model").strip(),
        "on_mains": on_mains(_out("pmset", "-g", "batt")),
        "power_mode": power_mode(_out("pmset", "-g")),
        **thermal(_out("pmset", "-g", "therm")),
        "gpu_wired_limit_mb": int(limit) if limit.isdigit() else None,
        "busy_processes": busy_processes(_out("ps", "-Ao", "pcpu=,comm=", "-r")),
    }
