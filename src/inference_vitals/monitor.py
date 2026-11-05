"""inference-vitals: a dashboard for running AI models on an Apple Silicon Mac.

  inference-vitals start     start macmon, the memory exporter, Prometheus and Grafana (each only if not running)
  inference-vitals status
  inference-vitals stop
  inference-vitals proxy --to http://127.0.0.1:11434    watch an engine (Ollama here); see proxy.py

Prometheus records every 5 seconds whether or not the dashboard is open, so a run can be looked at
afterwards with nothing drawing while it ran. Everything listens on 127.0.0.1 only. Configs, data and logs
live in ~/.local/state/inference-vitals (numbers only).
"""

import json
import os
import pathlib
import secrets
import signal
import subprocess
import sys
import time
import urllib.request

from inference_vitals.metrics import DEFAULT_PORT

STATE = pathlib.Path.home() / ".local/state/inference-vitals"
BREW = pathlib.Path("/opt/homebrew")
GRAFANA_HOME = BREW / "opt/grafana/share/grafana"
DATASOURCE_UID = "local-prom"
RUNNER, MACMON, SYSSTAT, PROMETHEUS, GRAFANA = DEFAULT_PORT, 9091, 9092, 9090, 3000

# What macmon 0.10 publishes (checked against `macmon serve` on 2026-11-05).
MACMON_METRICS = [
    "macmon_cpu_temp_celsius",
    "macmon_gpu_temp_celsius",
    "macmon_memory_ram_total_bytes",
    "macmon_memory_ram_used_bytes",
    "macmon_memory_swap_total_bytes",
    "macmon_memory_swap_used_bytes",
    "macmon_cpu_usage_ratio",
    "macmon_cpu_active_ratio",
    "macmon_ecpu_freq_mhz",
    "macmon_ecpu_active_ratio",
    "macmon_pcpu_freq_mhz",
    "macmon_pcpu_active_ratio",
    "macmon_gpu_freq_mhz",
    "macmon_gpu_usage_ratio",
    "macmon_gpu_active_ratio",
    "macmon_cpu_power_watts",
    "macmon_gpu_power_watts",
    "macmon_ane_power_watts",
    "macmon_all_power_watts",
    "macmon_sys_power_watts",
    "macmon_ram_power_watts",
    "macmon_fan_speed_rpm",
]


def prometheus_config() -> str:
    return f"""global:
  scrape_interval: 5s
scrape_configs:
  - job_name: inference-vitals
    static_configs: [{{targets: ["127.0.0.1:{RUNNER}"]}}]
  - job_name: macmon
    static_configs: [{{targets: ["127.0.0.1:{MACMON}"]}}]
  - job_name: mac-memory
    static_configs: [{{targets: ["127.0.0.1:{SYSSTAT}"]}}]
"""


def grafana_ini(state: pathlib.Path, admin_password: str) -> str:
    return f"""[paths]
data = {state}/data/grafana
logs = {state}/logs
provisioning = {state}/provisioning

[server]
http_addr = 127.0.0.1
http_port = {GRAFANA}

[security]
admin_password = {admin_password}
disable_gravatar = true

[auth.anonymous]
enabled = true
org_role = Viewer

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


def _target(exprs):
    return [{"refId": chr(65 + i), "expr": e, "legendFormat": legend} for i, (e, legend) in enumerate(exprs)]


def _panel(title, exprs, unit, x, y, w=12, h=8, kind="timeseries"):
    return {
        "type": kind,
        "title": title,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": {"type": "prometheus", "uid": DATASOURCE_UID},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "targets": _target(exprs),
    }


def _stat(title, expr, unit, x, y, warn=None, bad=None, legend="{{model_name}}", low_is_bad=False, w=3):
    """A big number with a small trend line under it. Colours: amber at `warn` and red at `bad` (or, with
    low_is_bad, red below `bad` and amber below `warn`)."""
    if low_is_bad:
        steps = [
            {"color": "red", "value": None},
            {"color": "orange", "value": bad},
            {"color": "green", "value": warn},
        ]
    else:
        steps = [{"color": "green", "value": None}]
        steps += [{"color": "orange", "value": warn}] if warn is not None else []
        steps += [{"color": "red", "value": bad}] if bad is not None else []
    p = _panel(title, [(expr, legend)], unit, x, y, w, 4, "stat")
    p["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute", "steps": steps}
    # A tile with nothing to show yet reads "–" in the text colour, not "No data" in the alarm colour.
    p["fieldConfig"]["defaults"]["noValue"] = "–"
    p["fieldConfig"]["defaults"]["mappings"] = [
        {"type": "special", "options": {"match": "null", "result": {"text": "–", "color": "text"}}}
    ]
    p["options"] = {
        "graphMode": "area",
        "colorMode": "value",
        "textMode": "value",
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
    }
    return p


def _row(title, y, collapsed=True, panels=()):
    return {
        "type": "row",
        "title": title,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "collapsed": collapsed,
        "panels": list(panels),
    }


TTFT_Q = (
    "histogram_quantile({q}, sum by (le, model_name) (rate(vllm:time_to_first_token_seconds_bucket[10m])))"
)
JOULES = (
    "sum(macmon_all_power_watts) / (sum(rate(vllm:generation_tokens_total[2m])) > 0)"  # no data when idle
)
SWAP_RATE = "rate(vitals_mac_swapped_in_bytes_total[1m]) + rate(vitals_mac_swapped_out_bytes_total[1m])"
HEADROOM = "vitals_gpu_memory_limit_bytes - vitals_peak_memory_bytes"
CONTEXT = "vitals_last_context_used_tokens / vitals_context_limit_tokens"
TOK_S = "suffix: tok/s"


def dashboard() -> dict:
    m = "{{model_name}}"
    live = _panel(
        "Writing speed now (tokens/s), against GPU temperature",
        [
            ("vitals_current_generation_tps", f"tokens/s {m}"),
            ("macmon_gpu_temp_celsius", "GPU °C"),
        ],
        TOK_S,
        0,
        9,
        24,
        9,
    )
    live["fieldConfig"]["overrides"] = [
        {
            "matcher": {"id": "byName", "options": "GPU °C"},
            "properties": [
                {"id": "custom.axisPlacement", "value": "right"},
                {"id": "unit", "value": "celsius"},
            ],
        }
    ]
    overview = [
        _row("At a glance", 0, collapsed=False),
        # The run: what is happening now, and anything going wrong with the answers.
        _stat(
            "Progress",
            "(vitals_pages_done + vitals_pages_already_answered) / vitals_pages_total",
            "percentunit",
            0,
            1,
        ),
        _stat("Writing now", "vitals_current_generation_tps", TOK_S, 3, 1),
        _stat("TTFT (median)", TTFT_Q.format(q=0.5), "s", 6, 1, warn=15, bad=30),
        _stat("Reading speed", "vitals_last_prompt_tps", TOK_S, 9, 1),
        _stat("Context used", CONTEXT, "percentunit", 12, 1, warn=0.75, bad=1.0),
        _stat("Answers cut off", "vitals_answers_cut_off_total", "none", 15, 1, warn=1, bad=5),
        _stat("Invalid answers", "vitals_answers_invalid_total", "none", 18, 1, warn=1, bad=5),
        _stat("Cold start TTFT", "vitals_cold_ttft_s", "s", 21, 1),
        # The Mac: memory first, since that is what fails first with a big model.
        _stat("GPU mem left", HEADROOM, "decbytes", 0, 5, warn=4e9, bad=2e9, low_is_bad=True),
        _stat(
            "Memory free",
            "vitals_mac_memory_free_percent",
            "percent",
            3,
            5,
            warn=30,
            bad=15,
            legend="",
            low_is_bad=True,
        ),
        _stat("Swap in/out", SWAP_RATE, "Bps", 6, 5, warn=10e6, bad=100e6, legend=""),
        _stat("GPU busy", "macmon_gpu_active_ratio", "percentunit", 9, 5, legend=""),
        _stat("GPU temp", "macmon_gpu_temp_celsius", "celsius", 12, 5, warn=85, bad=95, legend=""),
        _stat("Thermal", "vitals_last_thermal_warning", "none", 15, 5, warn=1, bad=2),
        _stat("Mac power", "macmon_sys_power_watts", "watt", 18, 5, legend=""),
        _stat("Energy/token", JOULES, "suffix: J/tok", 21, 5, legend=""),
        live,
    ]
    details = [
        _row(
            "Speed detail",
            18,
            panels=[
                _panel("Time to first token, per page", [("vitals_last_ttft_s", m)], "s", 0, 19),
                _panel(
                    "TTFT median and 95th percentile (10 min)",
                    [(TTFT_Q.format(q=0.5), f"p50 {m}"), (TTFT_Q.format(q=0.95), f"p95 {m}")],
                    "s",
                    12,
                    19,
                ),
                _panel(
                    "Writing speed (decode), per page, tokens/s",
                    [("vitals_last_generation_tps", m)],
                    TOK_S,
                    0,
                    27,
                ),
                _panel(
                    "Reading speed (prefill), per page, tokens/s",
                    [("vitals_last_prompt_tps", m)],
                    TOK_S,
                    12,
                    27,
                ),
                _panel(
                    "Tokens written so far on the current page",
                    [("vitals_current_generation_tokens", m)],
                    "none",
                    0,
                    35,
                ),
                _panel("Model load time", [("vitals_model_load_seconds", m)], "s", 12, 35),
            ],
        ),
        _row(
            "Context and limits",
            19,
            panels=[
                _panel(
                    "Context used per page, against the limit",
                    [
                        ("vitals_last_context_used_tokens", f"used {m}"),
                        ("vitals_context_limit_tokens", f"limit {m}"),
                    ],
                    "none",
                    0,
                    20,
                ),
                _panel(
                    "Tokens per page",
                    [
                        ("vitals_last_prompt_tokens", f"prompt {m}"),
                        ("vitals_last_cached_tokens", f"from cache {m}"),
                        ("vitals_last_generation_tokens", f"answer {m}"),
                    ],
                    "none",
                    12,
                    20,
                ),
                _panel(
                    "Answers cut off, invalid, over the context limit; requests failed",
                    [
                        ("vitals_answers_cut_off_total", f"cut off {m}"),
                        ("vitals_answers_invalid_total", f"invalid {m}"),
                        ("vitals_over_context_total", f"over context {m}"),
                        ("vitals_requests_failed_total", f"requests failed {m}"),
                    ],
                    "none",
                    0,
                    28,
                    24,
                ),
            ],
        ),
        _row(
            "Memory",
            20,
            panels=[
                _panel(
                    "MLX GPU memory, against the GPU limit",
                    [
                        ("vitals_active_memory_bytes", f"in use {m}"),
                        ("vitals_peak_memory_bytes", f"peak {m}"),
                        ("vitals_cache_memory_bytes", f"kept for reuse {m}"),
                        ("vitals_kv_cache_estimate_bytes", f"KV cache (estimate) {m}"),
                        ("vitals_gpu_memory_limit_bytes", f"GPU limit {m}"),
                    ],
                    "decbytes",
                    0,
                    21,
                ),
                _panel(
                    "Swap activity (in and out)",
                    [
                        ("rate(vitals_mac_swapped_in_bytes_total[1m])", "swapped in"),
                        ("rate(vitals_mac_swapped_out_bytes_total[1m])", "swapped out"),
                    ],
                    "Bps",
                    12,
                    21,
                ),
                _panel(
                    "Memory pressure (percent free, as macOS reports it)",
                    [
                        ("vitals_mac_memory_free_percent", "free"),
                    ],
                    "percent",
                    0,
                    29,
                ),
                _panel(
                    "Mac memory and swap in use",
                    [
                        ("macmon_memory_ram_used_bytes", "RAM used"),
                        ("macmon_memory_ram_total_bytes", "RAM total"),
                        ("vitals_mac_swap_used_bytes", "swap used"),
                    ],
                    "bytes",
                    12,
                    29,
                ),
            ],
        ),
        _row(
            "Hardware",
            21,
            panels=[
                _panel(
                    "Busy",
                    [
                        ("macmon_gpu_active_ratio", "GPU"),
                        ("macmon_pcpu_active_ratio", "performance cores"),
                        ("macmon_ecpu_active_ratio", "efficiency cores"),
                    ],
                    "percentunit",
                    0,
                    22,
                ),
                _panel(
                    "Power",
                    [
                        ("macmon_gpu_power_watts", "GPU"),
                        ("macmon_cpu_power_watts", "CPU"),
                        ("macmon_ane_power_watts", "Neural Engine"),
                        ("macmon_sys_power_watts", "whole Mac"),
                    ],
                    "watt",
                    12,
                    22,
                ),
                _panel(
                    "Temperature",
                    [("macmon_gpu_temp_celsius", "GPU"), ("macmon_cpu_temp_celsius", "CPU")],
                    "celsius",
                    0,
                    30,
                ),
                _panel(
                    "Clock speed",
                    [
                        ("macmon_gpu_freq_mhz", "GPU"),
                        ("macmon_pcpu_freq_mhz", "performance cores"),
                        ("macmon_ecpu_freq_mhz", "efficiency cores"),
                    ],
                    "none",
                    12,
                    30,
                ),
                _panel(
                    "Thermal warning and CPU speed limit",
                    [
                        ("vitals_last_thermal_warning", f"warning level {m}"),
                        ("vitals_last_cpu_speed_limit", f"CPU limit % {m}"),
                    ],
                    "none",
                    0,
                    38,
                ),
                _panel(
                    "Energy per answer token (approx., includes reading the prompt)",
                    [(JOULES, "joules per token")],
                    "none",
                    12,
                    38,
                ),
                _panel("Fans", [("macmon_fan_speed_rpm", "{{fan}}")], "rpm", 0, 46, 24),
            ],
        ),
    ]
    panel_id = 0
    for panel in overview + details:  # ids let a single panel be opened on its own (?viewPanel=<id>)
        for p in [panel, *panel.get("panels", [])]:
            panel_id += 1
            p["id"] = panel_id
    return {
        "uid": "inference-vitals",
        "title": "Inference Vitals",
        "schemaVersion": 39,
        "version": 3,
        "editable": True,
        "time": {"from": "now-3h", "to": "now"},
        "refresh": "10s",
        "tags": ["inference-vitals"],
        "panels": overview + details,
    }


def commands(state: pathlib.Path) -> dict:
    return {
        "macmon": [str(BREW / "bin/macmon"), "serve", "--host", "127.0.0.1", "--port", str(MACMON)],
        "sysstat": [sys.executable, "-m", "inference_vitals.sysstat", "--port", str(SYSSTAT)],
        "prometheus": [
            str(BREW / "bin/prometheus"),
            f"--config.file={state}/prometheus.yml",
            f"--storage.tsdb.path={state}/data/prometheus",
            "--storage.tsdb.retention.time=30d",
            f"--web.listen-address=127.0.0.1:{PROMETHEUS}",
        ],
        "grafana": [
            str(BREW / "bin/grafana"),
            "server",
            f"--homepath={GRAFANA_HOME}",
            f"--config={state}/grafana.ini",
        ],
    }


READY = {
    "macmon": f"http://127.0.0.1:{MACMON}/metrics",
    "sysstat": f"http://127.0.0.1:{SYSSTAT}/metrics",
    "prometheus": f"http://127.0.0.1:{PROMETHEUS}/-/ready",
    "grafana": f"http://127.0.0.1:{GRAFANA}/api/health",
}


def _write(path: pathlib.Path, text: str, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def write_configs(state: pathlib.Path = STATE):
    secret = state / "grafana-admin-password"
    if not secret.exists():  # used only when Grafana creates its database, on the first start
        _write(secret, secrets.token_urlsafe(12) + "\n", 0o600)
    _write(state / "prometheus.yml", prometheus_config())
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
    _write(state / "dashboards/inference-vitals.json", json.dumps(dashboard(), indent=1))
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


def start(state: pathlib.Path = STATE):
    write_configs(state)
    for name, cmd in commands(state).items():
        if _pid(state, name):
            print(f"{name}: already running", flush=True)
            continue
        log = open(state / "logs" / f"{name}.log", "a")  # noqa: SIM115 - handed to the child process
        proc = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=GRAFANA_HOME if name == "grafana" else state,
        )
        (state / "pids" / name).write_text(str(proc.pid))
        for _ in range(60):
            if _ready(READY[name]):
                break
            time.sleep(0.5)
        print(
            f"{name}: {'started' if _ready(READY[name]) else 'NOT READY, see ' + str(state / 'logs' / (name + '.log'))}",
            flush=True,
        )
    print(
        f"\nDashboard: http://127.0.0.1:{GRAFANA}/d/inference-vitals  (to edit, sign in as admin; the password is in "
        f"{state / 'grafana-admin-password'})"
    )


def stop(state: pathlib.Path = STATE):
    for name in reversed(list(commands(state))):
        pid = _pid(state, name)
        if pid:
            os.kill(pid, signal.SIGTERM)
            (state / "pids" / name).unlink()
        print(f"{name}: {'stopped' if pid else 'was not running'}")


def status(state: pathlib.Path = STATE):
    for name in commands(state):
        print(
            f"{name:11} {'running' if _pid(state, name) else 'stopped':8} {'answering' if _ready(READY[name]) else '-'}"
        )


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "proxy":
        from inference_vitals import proxy

        return proxy.main(sys.argv[2:])
    if action not in ("start", "stop", "status"):
        sys.exit(__doc__)
    {"start": start, "stop": stop, "status": status}[action]()
