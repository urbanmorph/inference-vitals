"""inference-vitals: a dashboard for running AI models on an Apple Silicon Mac, or on Linux with NVIDIA GPUs.

  inference-vitals start     start the chip and memory exporters, Prometheus and the dashboard page
                             (each only if not running); --ui grafana for Grafana instead of the page
  inference-vitals status
  inference-vitals stop
  inference-vitals proxy --to http://127.0.0.1:11434    watch an engine (Ollama here); see proxy.py
  inference-vitals train -- <training command>         follow a fine-tune as it runs; see follow.py
  inference-vitals watch-training <log file>            follow a fine-tune from its log

Prometheus records every 5 seconds whether or not the dashboard is open, so a run can be looked at
afterwards with nothing drawing while it ran. Everything listens on 127.0.0.1 only. Configs, data and logs
live in ~/.local/state/inference-vitals (numbers only).
"""

import json
import os
import pathlib
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from inference_vitals import __version__ as VERSION
from inference_vitals.grafana import DATASOURCE_UID, dashboard
from inference_vitals.metrics import DEFAULT_PORT
from inference_vitals.queries import LINUX, MAC, rules_config

STATE = pathlib.Path.home() / ".local/state/inference-vitals"
BREW = pathlib.Path("/opt/homebrew")
# CHIP is macmon on a Mac and the NVIDIA exporter (gpustat) on Linux; SYSSTAT is memory (and Linux CPU).
RUNNER, CHIP, SYSSTAT, PROMETHEUS, DASHBOARD = DEFAULT_PORT, 9091, 9092, 9090, 3000  # page or Grafana
PAGE_UI, GRAFANA_UI = "page", "grafana"  # the dashboard: our own page (default) or Grafana
GRAFANA_HOMES = [  # where packages put Grafana's own files (its "homepath")
    BREW / "opt/grafana/share/grafana",
    pathlib.Path("/usr/share/grafana"),
    pathlib.Path("/opt/grafana"),
    pathlib.Path("/usr/local/share/grafana"),
]


def prometheus_config() -> str:
    return f"""global:
  scrape_interval: 5s
rule_files: [rules.yml]
scrape_configs:
  - job_name: inference-vitals
    static_configs: [{{targets: ["127.0.0.1:{RUNNER}"]}}]
  - job_name: chip
    static_configs: [{{targets: ["127.0.0.1:{CHIP}"]}}]
  - job_name: host
    static_configs: [{{targets: ["127.0.0.1:{SYSSTAT}"]}}]
"""


def grafana_ini(state: pathlib.Path, admin_password: str) -> str:
    return f"""[paths]
data = {state}/data/grafana
logs = {state}/logs
provisioning = {state}/provisioning

[server]
http_addr = 127.0.0.1
http_port = {DASHBOARD}

[security]
admin_password = {admin_password}
disable_gravatar = true

# View-only: the dashboard is rebuilt from grafana.py on every start, so nobody signs in to edit it.
[auth]
disable_login_form = true

[auth.basic]
enabled = false

[auth.anonymous]
enabled = true
org_role = Viewer

# Nothing leaves the Mac: no snapshot upload to snapshots.raintank.io, no public links (which would
# point at 127.0.0.1 anyway), no plugin catalog, and none of the plugins Grafana otherwise downloads
# from grafana.com in the background on every start (the dashboard uses only built-in panels).
[snapshots]
external_enabled = false

[public_dashboards]
enabled = false

[plugins]
plugin_admin_enabled = false
preinstall_disabled = true

# No alerts are set up, so the alert engine would only use CPU during a run.
[unified_alerting]
enabled = false

[analytics]
reporting_enabled = false
check_for_updates = false
check_for_plugin_updates = false
feedback_links_enabled = false

[news]
news_feed_enabled = false

[dashboards]
default_home_dashboard_path = {state}/dashboards/inference-vitals.json
"""


def detect_platform(name: str = sys.platform) -> str:
    if name == "darwin":
        return MAC
    if name.startswith("linux"):
        return LINUX
    sys.exit(f"inference-vitals runs on macOS (Apple Silicon) or Linux, not {name}")


def find_tools(platform: str, path: str | None = None, homes: list | None = None) -> dict:
    """Where Prometheus, Grafana (and its home folder) and macmon are, from PATH; None for any missing."""
    search = path if path is not None else os.pathsep.join([os.environ.get("PATH", ""), str(BREW / "bin")])
    grafana = shutil.which("grafana", path=search) or shutil.which("grafana-server", path=search)
    beside = [pathlib.Path(grafana).resolve().parent.parent / "share/grafana"] if grafana else []
    home = next(
        (h for h in [*beside, *(homes if homes is not None else GRAFANA_HOMES)] if (h / "conf").is_dir()),
        None,
    )
    return {
        "prometheus": shutil.which("prometheus", path=search),
        "grafana": grafana,
        "grafana_home": str(home) if home else None,
        "macmon": shutil.which("macmon", path=search) if platform == MAC else None,
        "nvidia": platform == LINUX and shutil.which("nvidia-smi", path=search) is not None,
    }


def commands(state: pathlib.Path, platform: str = MAC, tools: dict | None = None, ui: str = PAGE_UI) -> dict:
    t = tools or find_tools(platform)
    cmds = {}
    if platform == MAC:
        cmds["macmon"] = [t["macmon"], "serve", "--host", "127.0.0.1", "--port", str(CHIP)]
    elif t["nvidia"]:
        cmds["gpustat"] = [sys.executable, "-m", "inference_vitals.gpustat", "--port", str(CHIP)]
    cmds["sysstat"] = [sys.executable, "-m", "inference_vitals.sysstat", "--port", str(SYSSTAT)]
    cmds["prometheus"] = [
        t["prometheus"],
        f"--config.file={state}/prometheus.yml",
        f"--storage.tsdb.path={state}/data/prometheus",
        "--storage.tsdb.retention.time=30d",
        f"--web.listen-address=127.0.0.1:{PROMETHEUS}",
    ]
    if ui == GRAFANA_UI:
        grafana = [t["grafana"]] + ([] if str(t["grafana"]).endswith("grafana-server") else ["server"])
        cmds["grafana"] = [*grafana, f"--homepath={t['grafana_home']}", f"--config={state}/grafana.ini"]
    else:
        cmds["page"] = [
            sys.executable,
            "-m",
            "inference_vitals.ui",
            "--port",
            str(DASHBOARD),
            "--platform",
            platform,
        ]
    return cmds


READY = {
    "macmon": f"http://127.0.0.1:{CHIP}/metrics",
    "gpustat": f"http://127.0.0.1:{CHIP}/metrics",
    "sysstat": f"http://127.0.0.1:{SYSSTAT}/metrics",
    "prometheus": f"http://127.0.0.1:{PROMETHEUS}/-/ready",
    "grafana": f"http://127.0.0.1:{DASHBOARD}/api/health",
    "page": f"http://127.0.0.1:{DASHBOARD}/api/spec",
}


def _write(path: pathlib.Path, text: str, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def write_configs(state: pathlib.Path = STATE, platform: str = MAC):
    secret = state / "grafana-admin-password"
    # Sign-in is off, but Grafana still creates an admin account on first start; a random password
    # keeps it from being the default admin/admin if someone turns sign-in back on.
    if not secret.exists():
        _write(secret, secrets.token_urlsafe(12) + "\n", 0o600)
    _write(state / "prometheus.yml", prometheus_config())
    _write(state / "rules.yml", rules_config(platform))
    _write(state / "grafana.ini", grafana_ini(state, secret.read_text().strip()), 0o600)
    _write(
        state / "provisioning/datasources/prometheus.yml",
        f"""apiVersion: 1
datasources:
  - name: Prometheus (local)
    uid: {DATASOURCE_UID}
    type: prometheus
    url: http://127.0.0.1:{PROMETHEUS}
    isDefault: true
""",
    )
    _write(
        state / "provisioning/dashboards/inference-vitals.yml",
        f"""apiVersion: 1
providers:
  - name: inference-vitals
    type: file
    options:
      path: {state}/dashboards
""",
    )
    _write(state / "dashboards/inference-vitals.json", json.dumps(dashboard(platform), indent=1))
    for d in ("data/prometheus", "data/grafana", "logs", "pids"):
        (state / d).mkdir(parents=True, exist_ok=True)


def _pid(state, name):
    f = state / "pids" / name
    if not f.exists():
        return None
    pid = int(f.read_text())
    try:
        os.kill(pid, 0)
        return pid
    except ProcessLookupError:
        f.unlink()
        return None


def _ready(url):
    try:
        with urllib.request.urlopen(url, timeout=1):
            return True
    except OSError:
        return False


def missing_tools(platform: str, tools: dict, ui: str = PAGE_UI) -> list[str]:
    """One line per program `start` needs and cannot find, saying how to get it."""
    how = "brew install" if platform == MAC else "see the README's Linux section"
    needed = (
        ["prometheus"] + (["grafana"] if ui == GRAFANA_UI else []) + (["macmon"] if platform == MAC else [])
    )
    out = [
        f"{name}: not found ({how} {name})" if platform == MAC else f"{name}: not found ({how})"
        for name in needed
        if not tools.get(name)
    ]
    if ui == GRAFANA_UI and tools.get("grafana") and not tools.get("grafana_home"):
        out.append("grafana: its home folder (with conf/) was not found beside it or in the usual places")
    return out


def chosen_ui(args: list[str]) -> str:
    """`start` takes `--ui grafana` to use Grafana instead of the page."""
    if not args:
        return PAGE_UI
    if args[:1] == ["--ui"] and args[1:] in ([PAGE_UI], [GRAFANA_UI]):
        return args[1]
    sys.exit(f"usage: inference-vitals start [--ui {GRAFANA_UI}]")


def ui_conflict(state: pathlib.Path, ui: str):
    """Why this dashboard cannot start (the other one holds its port), or None."""
    other = GRAFANA_UI if ui == PAGE_UI else PAGE_UI
    if _pid(state, other):
        return f"{other} is running on port {DASHBOARD}: run `inference-vitals stop` first, then start again"
    return None


def dashboard_url(ui: str) -> str:
    return f"http://127.0.0.1:{DASHBOARD}/" + ("d/inference-vitals" if ui == GRAFANA_UI else "")


def running_version(state: pathlib.Path):
    """The version that started the services now running: None if nothing runs, "an older version" for
    services started before versions were recorded (0.2.0)."""
    if not any(_pid(state, name) for name in _names(detect_platform())):
        return None
    f = state / "version"
    return f.read_text().strip() if f.exists() else "an older version"


def _port(name: str) -> int:
    return urllib.parse.urlsplit(READY[name]).port


def _taken(name: str) -> str:
    """What to say when another program already answers on a service's port."""
    usual = {
        "prometheus": "Often Prometheus run by Homebrew: `brew services stop prometheus`",
        "macmon": "Often macmon run by Homebrew: `brew services stop macmon`",
    }.get(name, "Perhaps another copy of inference-vitals: `inference-vitals stop` there")
    return f"port {_port(name)} is already in use by another program. {usual}, then start again."


def _last_line(path: pathlib.Path) -> str:
    lines = (
        [ln.strip() for ln in path.read_text(errors="replace").splitlines() if ln.strip()]
        if path.exists()
        else []
    )
    return lines[-1][:200] if lines else "no output"


def start(state: pathlib.Path = STATE, ui: str = PAGE_UI):
    platform = detect_platform()
    tools = find_tools(platform)
    if missing := missing_tools(platform, tools, ui):
        sys.exit("\n".join(missing))
    if conflict := ui_conflict(state, ui):
        sys.exit(conflict)
    if platform == LINUX and not tools["nvidia"]:
        print(
            "nvidia-smi not found: GPU numbers stay empty (the model and memory numbers still work)",
            flush=True,
        )
    if (was := running_version(state)) not in (None, VERSION):
        # after `uv tool upgrade`: the services still run the old code and the old settings
        who = f"inference-vitals {was}" if was[0].isdigit() else "An older inference-vitals"
        print(f"{who} is running; restarting as {VERSION}", flush=True)
        stop(state)
    write_configs(state, platform)
    (state / "version").write_text(VERSION)
    failed, started = False, 0
    for name, cmd in commands(state, platform, tools, ui).items():
        if _pid(state, name):
            print(f"{name}: already running", flush=True)
            continue
        if _ready(READY[name]):  # ours is not running, yet something answers there
            print(f"{name}: {_taken(name)}", flush=True)
            failed = True
            continue
        log_path = state / "logs" / f"{name}.log"
        log = open(log_path, "a")  # noqa: SIM115 - handed to the child process
        proc = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=tools["grafana_home"] if name == "grafana" else state,
        )
        (state / "pids" / name).write_text(str(proc.pid))
        for _ in range(60):
            if proc.poll() is not None or _ready(READY[name]):
                break
            time.sleep(0.5)
        if proc.poll() is not None:  # it exited: "started" only for a process of ours that is still there
            (state / "pids" / name).unlink()
            print(f"{name}: did not start: {_last_line(log_path)} (log: {log_path})", flush=True)
            failed = True
        elif not _ready(READY[name]):
            print(f"{name}: NOT READY, see {log_path}", flush=True)
            failed = True
        else:
            print(f"{name}: started", flush=True)
            started += 1
    if failed:
        rest = (
            "The others are running (`inference-vitals stop` stops them)."
            if started
            else "Nothing was started."
        )
        sys.exit(f"\nNot everything could start; see above. {rest}")
    print(
        f"\nDashboard: {dashboard_url(ui)}  (view-only; change it in {'grafana.py' if ui == GRAFANA_UI else 'ui.py'})"
    )


def _names(platform: str) -> list[str]:
    chip = "macmon" if platform == MAC else "gpustat"
    return [chip, "sysstat", "prometheus", PAGE_UI, GRAFANA_UI]


def _gone(pid: int) -> bool:
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:  # our own child (started by this process) has exited
            return True
    except ChildProcessError:  # not our child: launchd or init reaps it
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def stop(state: pathlib.Path = STATE):
    for name in reversed(_names(detect_platform())):
        pid = _pid(state, name)
        if pid:
            os.kill(pid, signal.SIGTERM)
            (state / "pids" / name).unlink()
            for _ in range(100):  # until it has gone, so a start straight after finds its port free
                if _gone(pid):
                    break
                time.sleep(0.1)
        print(f"{name}: {'stopped' if pid else 'was not running'}")


def status(state: pathlib.Path = STATE):
    print(f"inference-vitals {VERSION}")
    if (was := running_version(state)) not in (None, VERSION):
        print(f"{was} is still running: run `inference-vitals start` to switch")
    for name in _names(detect_platform()):
        ours, answers = _pid(state, name), _ready(READY[name])
        if not ours and answers:
            print(f"{name:11} stopped  port in use by another program ({_port(name)})")
        else:
            print(f"{name:11} {'running' if ours else 'stopped':8} {'answering' if answers else '-'}")


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action in ("--version", "version"):
        return print(VERSION)
    if action == "proxy":
        from inference_vitals import proxy

        return proxy.main(sys.argv[2:])
    if action in ("train", "watch-training"):
        from inference_vitals import follow

        return follow.main(action, sys.argv[2:])
    if action not in ("start", "stop", "status"):
        sys.exit(__doc__)
    if action == "start":
        return start(ui=chosen_ui(sys.argv[2:]))
    {"stop": stop, "status": status}[action]()
