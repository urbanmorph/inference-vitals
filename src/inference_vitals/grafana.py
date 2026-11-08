"""The Grafana dashboard (`start --ui grafana`), built from code and rebuilt on every start. The page's tabs (ui.py) are taken from this dashboard's rows, so both show the same charts."""

from inference_vitals.queries import CONTEXT, HW, LINUX, MAC, PHASE_NAMES, SWAP_RATE, TTFT_Q

REPO = "https://github.com/urbanmorph/inference-vitals"
DATASOURCE_UID = "local-prom"
TOK_S = "suffix: tok/s"


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


TILE_H, STRIP_H = 3, 2  # a number, and under it a small trend strip (a graph behind the number hides it)
BAND_H = 2  # "Running": each model and what it is doing, above the tiles
ROW1 = 1 + BAND_H
ROW2 = ROW1 + TILE_H + STRIP_H
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


def _training_panels() -> list:
    """A fine-tune, as inference_vitals.training publishes it (directly, or followed from a trainer's log)."""
    r = "{{run_name}}"
    return [
        _panel(
            "Training and validation loss",
            [
                ("vitals_training_loss", f"train loss {r}"),
                ("vitals_training_val_loss", f"validation loss {r}"),
            ],
            "none",
            0,
            0,
        ),
        _panel("Tokens trained per second", [("vitals_training_tokens_per_second", r)], TOK_S, 12, 0),
        _panel("Seconds per step", [("vitals_training_step_seconds", r)], "s", 0, 8),
        _panel("Learning rate", [("vitals_training_learning_rate", r)], "none", 12, 8),
        _panel("Peak memory of the trainer", [("vitals_training_peak_memory_bytes", r)], "decbytes", 0, 16),
    ]


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
        "Energy per answer token (the model's share of power ÷ tokens, over 2 min)",
        [(HW[platform]["joules"], "joules per token")],
        "suffix: J/tok",
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
        _row("Training", 20, panels=_training_panels()),  # with the model's sections, before the machine's
        _row("Memory", 21, panels=_memory_panels(platform, m)),
        _row("Hardware", 22, panels=_hardware_panels(platform, m)),
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
    # ids let a single panel be opened on its own (?viewPanel=<id>); panels added later number after the
    # rest (the band, then Training) so the ids already in use stay put wherever a row sits
    later = [r for r in details if r["title"] == "Training"]
    for panel in overview + [r for r in details if r not in later] + [running] + later:
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
