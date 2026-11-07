"""inference-vitals: a dashboard for running AI models on an Apple Silicon Mac, or on Linux with NVIDIA GPUs.

  inference-vitals start     start the chip and memory exporters, Prometheus and Grafana (each only if not running)
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
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

from inference_vitals.metrics import DEFAULT_PORT

STATE = pathlib.Path.home() / ".local/state/inference-vitals"
REPO = "https://github.com/urbanmorph/inference-vitals"
BREW = pathlib.Path("/opt/homebrew")
DATASOURCE_UID = "local-prom"
# CHIP is macmon on a Mac and the NVIDIA exporter (gpustat) on Linux; SYSSTAT is memory (and Linux CPU).
RUNNER, CHIP, SYSSTAT, PROMETHEUS, GRAFANA = DEFAULT_PORT, 9091, 9092, 9090, 3000
MAC, LINUX = "mac", "linux"
PLATFORMS = (MAC, LINUX)
GRAFANA_HOMES = [  # where packages put Grafana's own files (its "homepath")
    BREW / "opt/grafana/share/grafana",
    pathlib.Path("/usr/share/grafana"),
    pathlib.Path("/opt/grafana"),
    pathlib.Path("/usr/local/share/grafana"),
]

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
http_port = {GRAFANA}

[security]
admin_password = {admin_password}
disable_gravatar = true

# View-only: the dashboard is rebuilt from monitor.py on every start, so nobody signs in to edit it.
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


THERMAL_NAMES = ["Nominal", "Fair", "Serious", "Critical"]
TILE_H, STRIP_H = 3, 2  # a number, and under it a small trend strip (a graph behind the number hides it)
BAND_H = 2  # "Running": each model and what it is doing, above the tiles
ROW1 = 1 + BAND_H
ROW2 = ROW1 + TILE_H + STRIP_H
PHASE_NAMES = ["Idle", "Reading (prefill)", "Writing (decode)"]  # metrics.IDLE, READING, WRITING
STRIP_COLOUR = "rgba(140, 140, 140, 0.7)"
STRIP_IDS = 101  # strips are numbered apart, so the detail panels keep their ids (?viewPanel=31)


def _strip(tile: dict) -> dict:
    """A frameless line chart under a tile: the same numbers over time, no title, axes or legend."""
    g = tile["gridPos"]
    s = _panel("", [], tile["fieldConfig"]["defaults"]["unit"], g["x"], g["y"] + g["h"], g["w"], STRIP_H)
    s["targets"] = [{**t, "instant": False, "range": True} for t in tile["targets"]]  # the history
    s["transparent"] = True
    stepped = any(m["type"] == "value" for m in tile["fieldConfig"]["defaults"]["mappings"])
    # Quiet grey, so the eye goes to the number; Grafana cannot fade a graph drawn inside the tile itself.
    s["fieldConfig"]["defaults"]["color"] = {"mode": "fixed", "fixedColor": STRIP_COLOUR}
    s["fieldConfig"]["defaults"]["custom"] = {
        "axisPlacement": "hidden",
        "lineWidth": 1,
        "fillOpacity": 8,
        "gradientMode": "opacity",
        "showPoints": "never",
        "spanNulls": True,
        "lineInterpolation": "stepAfter" if stepped else "linear",
    }
    s["options"] = {"legend": {"showLegend": False}, "tooltip": {"mode": "multi"}}
    return s


def _stat(
    title,
    expr,
    unit,
    x,
    y,
    warn=None,
    bad=None,
    legend="{{model_name}}",
    low_is_bad=False,
    w=3,
    names=None,
    h=TILE_H,
):
    """A big number on its own; trends are in the chart below and the folded sections. Colours: amber at `warn` and red at `bad` (or, with
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
    p = _panel(title, [(expr, legend)], unit, x, y, w, h, "stat")
    for t in p["targets"]:  # now, not "last value in the time range": a finished run drops off at once
        t["instant"], t["range"] = True, False
    p["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute", "steps": steps}
    # A tile with nothing to show yet reads "–" in the text colour, not "No data" in the alarm colour.
    p["fieldConfig"]["defaults"]["noValue"] = "–"
    p["fieldConfig"]["defaults"]["mappings"] = [
        {"type": "special", "options": {"match": "null", "result": {"text": "–", "color": "text"}}}
    ]
    if names:  # numbers that stand for states: show the state's name
        p["fieldConfig"]["defaults"]["mappings"].append(
            {"type": "value", "options": {str(n): {"text": t, "index": n} for n, t in enumerate(names)}}
        )
    p["options"] = {
        "graphMode": "none",
        "colorMode": "value",
        "textMode": "auto",
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
SLOWDOWN_NAMES = ["None", "Power cap", "Thermal", "Hardware"]  # gpustat.slowdown_state
PER_TOKEN = " / (sum(rate(vllm:generation_tokens_total[2m])) > 0)"  # no data when idle
# What each platform measures the machine with. The model half of the dashboard is the same everywhere.
HW = {
    MAC: {
        # One machine, so max(): a tile shows one number even if Prometheus briefly holds two copies.
        "gpu_busy": "max(macmon_gpu_active_ratio)",
        "gpu_temp": "max(macmon_gpu_temp_celsius)",
        "gpu_mem_left": "min(vitals_gpu_memory_limit_bytes - vitals_peak_memory_bytes)",  # the tightest model
        "slowdown": ("Thermal", "max(vitals_host_thermal_state)", THERMAL_NAMES, 1, 2),
        "power": ("Mac power", "max(macmon_sys_power_watts)"),
        "joules": "max(macmon_all_power_watts)" + PER_TOKEN,
    },
    LINUX: {
        "gpu_busy": "max(vitals_gpu_busy_ratio)",
        "gpu_temp": "max(vitals_gpu_temp_celsius)",
        "gpu_mem_left": "sum(vitals_gpu_memory_free_bytes)",
        "slowdown": ("GPU slowdown", "max(vitals_gpu_slowdown_state)", SLOWDOWN_NAMES, 2, 3),
        "power": ("GPU power", "sum(vitals_gpu_power_watts)"),
        "joules": "sum(vitals_gpu_power_watts)"
        + PER_TOKEN,  # the GPUs only: Linux has no whole-machine meter
    },
}
SWAP_RATE = (
    "max(rate(vitals_host_swapped_in_bytes_total[1m])) + max(rate(vitals_host_swapped_out_bytes_total[1m]))"
)
# live while a page is written (page + tokens so far); between pages, the last page in full
CONTEXT = "(vitals_current_context_tokens or vitals_last_context_used_tokens) / vitals_context_limit_tokens"
TOK_S = "suffix: tok/s"


def _flow(panels: list, y: int):
    """Lay panels out in their listed order: a full-width one on its own line, half-width ones in pairs."""
    x = 0
    for p in panels:
        g = p["gridPos"]
        if x + g["w"] > 24:
            x, y = 0, y + 8
        g["x"], g["y"] = x, y
        x += g["w"]
        if x >= 24:
            x, y = 0, y + g["h"]


def _memory_panels(platform: str, m: str) -> list:
    swap = _panel(
        "Swap activity (in and out)",
        [
            ("rate(vitals_host_swapped_in_bytes_total[1m])", "swapped in"),
            ("rate(vitals_host_swapped_out_bytes_total[1m])", "swapped out"),
        ],
        "Bps",
        12,
        21,
    )
    if platform == LINUX:
        return [
            _panel(
                "GPU memory (VRAM), used against total",
                [
                    ("vitals_gpu_memory_used_bytes", "used, GPU {{gpu}}"),
                    ("vitals_gpu_memory_total_bytes", "total, GPU {{gpu}}"),
                ],
                "bytes",
                0,
                21,
            ),
            swap,
            _panel(
                "Memory available (percent)",
                [("vitals_host_memory_free_percent", "available")],
                "percent",
                0,
                29,
            ),
            _panel("Swap in use", [("vitals_host_swap_used_bytes", "swap used")], "bytes", 12, 29),
        ]
    return [
        _panel(
            "MLX GPU memory, against the GPU limit",
            [
                ("vitals_active_memory_bytes", f"in use {m}"),
                ("vitals_peak_memory_bytes", f"peak {m}"),
                ("vitals_cache_memory_bytes", f"kept for reuse {m}"),
                ("vitals_kv_cache_estimate_bytes", f"KV cache (estimate) {m}"),
                ("max(vitals_gpu_memory_limit_bytes)", "GPU limit"),  # the Mac's, not a model's
            ],
            "decbytes",
            0,
            21,
        ),
        swap,
        _panel(
            "Memory pressure (percent free, as macOS reports it)",
            [("vitals_host_memory_free_percent", "free")],
            "percent",
            0,
            29,
        ),
        _panel(
            "Mac memory and swap in use",
            [
                ("macmon_memory_ram_used_bytes", "RAM used"),
                ("macmon_memory_ram_total_bytes", "RAM total"),
                ("vitals_host_swap_used_bytes", "swap used"),
            ],
            "bytes",
            12,
            29,
        ),
    ]


def _hardware_panels(platform: str, m: str) -> list:
    energy = _panel(
        "Energy per answer token (approx., includes reading the prompt)"
        + (" — GPUs only" if platform == LINUX else ""),
        [(HW[platform]["joules"], "joules per token")],
        "none",
        12,
        38,
    )
    if platform == LINUX:
        g = "GPU {{gpu}}"
        return [
            _panel(
                "Busy",
                [("vitals_gpu_busy_ratio", g), ("vitals_host_cpu_busy_ratio", "CPU")],
                "percentunit",
                0,
                22,
            ),
            _panel(
                "GPU power, against its limit",
                [("vitals_gpu_power_watts", g), ("vitals_gpu_power_limit_watts", f"limit, {g}")],
                "watt",
                12,
                22,
            ),
            _panel(
                "Temperature",
                [("vitals_gpu_temp_celsius", g), ("vitals_host_cpu_temp_celsius", "CPU")],
                "celsius",
                0,
                30,
            ),
            _panel(
                "Clock speed",
                [("vitals_gpu_freq_mhz", g), ("vitals_gpu_memory_freq_mhz", f"memory, {g}")],
                "none",
                12,
                30,
            ),
            _panel(
                "GPU slowdown (0 none, 1 power cap, 2 thermal, 3 hardware)",
                [("vitals_gpu_slowdown_state", g)],
                "none",
                0,
                38,
            ),
            energy,
            _panel("GPU fans", [("vitals_gpu_fan_ratio", g)], "percentunit", 0, 46, 24),
        ]
    return [
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
            "Thermal state, warning and CPU speed limit",
            [
                ("vitals_host_thermal_state", "thermal state (0 nominal to 3 critical)"),
                ("vitals_last_thermal_warning", f"warning level {m}"),
                ("vitals_last_cpu_speed_limit", f"CPU limit % {m}"),
            ],
            "none",
            0,
            38,
        ),
        energy,
        _panel("Fans", [("macmon_fan_speed_rpm", "{{fan}}")], "rpm", 0, 46, 24),
    ]


def dashboard(platform: str = MAC) -> dict:
    hw = HW[platform]
    slow = hw["slowdown"]
    m = "{{model_name}}"
    live = _panel(
        "Writing speed now (tokens/s), against GPU temperature",
        [
            ("vitals_current_generation_tps", f"tokens/s {m}"),
            (hw["gpu_temp"], "GPU °C"),
        ],
        TOK_S,
        0,
        ROW2 + TILE_H + STRIP_H,
        24,
        7,
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
    running = _stat("Running", "vitals_current_phase", "none", 0, 1, w=24, names=PHASE_NAMES, h=BAND_H)
    running["options"].update(
        textMode="value_and_name", colorMode="none", text={"titleSize": 16, "valueSize": 16}
    )
    overview = [
        _row("At a glance", 0, collapsed=False),
        # The run: what is happening now, and anything going wrong with the answers.
        _stat(
            "Progress",
            "(vitals_pages_done + vitals_pages_already_answered) / vitals_pages_total",
            "percentunit",
            0,
            ROW1,
        ),
        _stat("Writing now", "vitals_current_generation_tps", TOK_S, 3, ROW1),
        _stat("TTFT (median)", TTFT_Q.format(q=0.5), "s", 6, ROW1, warn=15, bad=30),
        _stat("Reading speed", "vitals_last_prompt_tps", TOK_S, 9, ROW1),
        _stat("Context used", CONTEXT, "percentunit", 12, ROW1, warn=0.75, bad=1.0),
        _stat("Answers cut off", "vitals_answers_cut_off_total", "none", 15, ROW1, warn=1, bad=5),
        _stat("Invalid answers", "vitals_answers_invalid_total", "none", 18, ROW1, warn=1, bad=5),
        _stat("Cold start TTFT", "vitals_cold_ttft_s", "s", 21, ROW1),
        # The machine: memory first, since that is what fails first with a big model.
        _stat(
            "GPU mem left",
            hw["gpu_mem_left"],
            "decbytes",
            0,
            ROW2,
            warn=4e9,
            bad=2e9,
            legend="",
            low_is_bad=True,
        ),
        _stat(
            "Memory free",
            "max(vitals_host_memory_free_percent)",
            "percent",
            3,
            ROW2,
            warn=30,
            bad=15,
            legend="",
            low_is_bad=True,
        ),
        _stat("Swap in/out", SWAP_RATE, "Bps", 6, ROW2, warn=10e6, bad=100e6, legend=""),
        _stat("GPU busy", hw["gpu_busy"], "percentunit", 9, ROW2, legend=""),
        _stat("GPU temp", hw["gpu_temp"], "celsius", 12, ROW2, warn=85, bad=95, legend=""),
        _stat(slow[0], slow[1], "none", 15, ROW2, warn=slow[3], bad=slow[4], legend="", names=slow[2]),
        _stat(hw["power"][0], hw["power"][1], "watt", 18, ROW2, legend=""),
        _stat("Energy/token", hw["joules"], "suffix: J/tok", 21, ROW2, legend=""),
        live,
    ]
    overview += [_strip(p) for p in overview if p["type"] == "stat"]
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
        _row("Memory", 20, panels=_memory_panels(platform, m)),
        _row("Hardware", 21, panels=_hardware_panels(platform, m)),
    ]
    for row in details:
        for p in row["panels"]:
            # Several lines per model make long names: list them in a table beside the chart, with their
            # latest and highest values, and give the chart the full width so the table has room.
            if len(p["targets"]) >= 2 and any("{{model_name}}" in t["legendFormat"] for t in p["targets"]):
                p["options"] = {
                    "legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]},
                    "tooltip": {"mode": "multi"},
                }
                p["gridPos"]["w"] = 24
        _flow(row["panels"], row["gridPos"]["y"] + 1)
    shift = live["gridPos"]["y"] + live["gridPos"]["h"] - 18  # details were laid out to start at y=18
    for row in details:
        for p in [row, *row["panels"]]:
            p["gridPos"]["y"] += shift
    panel_id, strip_id = 0, STRIP_IDS
    # ids let a single panel be opened on its own (?viewPanel=<id>); the band, added later, numbers last
    # so the ids already in use stay put
    for panel in overview + details + [running]:
        for p in [panel, *panel.get("panels", [])]:
            if p["title"] == "":
                p["id"], strip_id = strip_id, strip_id + 1
            else:
                panel_id += 1
                p["id"] = panel_id
    return {
        "uid": "inference-vitals",
        "title": "Inference Vitals",
        "links": [
            {
                "title": "inference-vitals on GitHub",
                "type": "link",
                "url": REPO,
                "icon": "external link",
                "targetBlank": True,
                "tooltip": "Source, setup and what each number means",
                "asDropdown": False,
                "includeVars": False,
                "keepTime": False,
                "tags": [],
            }
        ],
        "schemaVersion": 39,
        "version": 3,
        "editable": True,
        "time": {"from": "now-3h", "to": "now"},
        "refresh": "10s",
        "tags": ["inference-vitals"],
        "panels": [overview[0], running, *overview[1:], *details],
    }


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


def commands(state: pathlib.Path, platform: str = MAC, tools: dict | None = None) -> dict:
    t = tools or find_tools(platform)
    grafana = [t["grafana"]] + ([] if str(t["grafana"]).endswith("grafana-server") else ["server"])
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
    cmds["grafana"] = [*grafana, f"--homepath={t['grafana_home']}", f"--config={state}/grafana.ini"]
    return cmds


READY = {
    "macmon": f"http://127.0.0.1:{CHIP}/metrics",
    "gpustat": f"http://127.0.0.1:{CHIP}/metrics",
    "sysstat": f"http://127.0.0.1:{SYSSTAT}/metrics",
    "prometheus": f"http://127.0.0.1:{PROMETHEUS}/-/ready",
    "grafana": f"http://127.0.0.1:{GRAFANA}/api/health",
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


def missing_tools(platform: str, tools: dict) -> list[str]:
    """One line per program `start` needs and cannot find, saying how to get it."""
    how = "brew install" if platform == MAC else "see the README's Linux section"
    needed = ["prometheus", "grafana"] + (["macmon"] if platform == MAC else [])
    out = [
        f"{name}: not found ({how} {name})" if platform == MAC else f"{name}: not found ({how})"
        for name in needed
        if not tools.get(name)
    ]
    if tools.get("grafana") and not tools.get("grafana_home"):
        out.append("grafana: its home folder (with conf/) was not found beside it or in the usual places")
    return out


def start(state: pathlib.Path = STATE):
    platform = detect_platform()
    tools = find_tools(platform)
    if missing := missing_tools(platform, tools):
        sys.exit("\n".join(missing))
    if platform == LINUX and not tools["nvidia"]:
        print(
            "nvidia-smi not found: GPU numbers stay empty (the model and memory numbers still work)",
            flush=True,
        )
    write_configs(state, platform)
    for name, cmd in commands(state, platform, tools).items():
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
            cwd=tools["grafana_home"] if name == "grafana" else state,
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
    print(f"\nDashboard: http://127.0.0.1:{GRAFANA}/d/inference-vitals  (view-only; change it in monitor.py)")


def _names(platform: str) -> list[str]:
    chip = "macmon" if platform == MAC else "gpustat"
    return [chip, "sysstat", "prometheus", "grafana"]


def stop(state: pathlib.Path = STATE):
    for name in reversed(_names(detect_platform())):
        pid = _pid(state, name)
        if pid:
            os.kill(pid, signal.SIGTERM)
            (state / "pids" / name).unlink()
        print(f"{name}: {'stopped' if pid else 'was not running'}")


def status(state: pathlib.Path = STATE):
    for name in _names(detect_platform()):
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
