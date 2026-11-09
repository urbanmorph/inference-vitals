"""inference-vitals' own page: the dashboard without Grafana, served on 127.0.0.1 only.

The page is plain HTML, CSS and JavaScript from this package (nothing is fetched from the internet). It
reads Prometheus through two read-only routes, /api/v1/query and /api/v1/query_range, passed through with
only their own parameters; nothing else reaches Prometheus and nothing can be written.

What the page shows comes from `spec(platform)`: the tiles (each with the gauge that suits its number),
a model run's band and a fine-tune's, the energy used in the chosen range and how much of it was the AI,
the battery, and the charts: each defined once (`charts`), grouped in tabs that follow the Grafana
dashboard's rows (`views`), with the charts Favourites starts with (`favourites`).

  python -m inference_vitals.ui --port 3000 --platform mac
"""

import argparse
import http.server
import json
import pathlib
import threading
import urllib.error
import urllib.parse
import urllib.request

from inference_vitals import monitor, training
from inference_vitals.grafana import REPO, dashboard
from inference_vitals.metrics import Server, run_until_interrupt
from inference_vitals.queries import (
    ACTIVE,
    CONTEXT,
    HW,
    IDLE,
    LIMITS,
    LINUX,
    MAC,
    PHASE_NAMES,
    RUN_ACTIVE,
    SWAP_RATE,
    TRAIN_ACTIVE,
    TTFT_Q,
)

PAGE = pathlib.Path(__file__).parent / "page"
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/page.js": ("page.js", "text/javascript; charset=utf-8"),
    "/page.css": ("page.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
FORWARD = {  # the only Prometheus routes the page may use, each with only its own parameters
    "/api/v1/query": ("query", "time"),
    "/api/v1/query_range": ("query", "start", "end", "step"),
}
# The time ranges the page offers: label, length and the step of the energy subqueries (coarser for long
# ranges, so a week's sum stays cheap). Queries carry {range}, {step} and {base}, filled in by the page.
RANGES = [
    {"key": "15m", "label": "15 min", "seconds": 900, "step": 15},
    {"key": "1h", "label": "1 h", "seconds": 3600, "step": 15},
    {"key": "6h", "label": "6 h", "seconds": 21600, "step": 15},
    {"key": "24h", "label": "24 h", "seconds": 86400, "step": 60},
    {"key": "7d", "label": "7 d", "seconds": 604800, "step": 300},
]


def _energy(platform: str) -> dict:
    """Energy over the chosen range: all of it, and the model's share, which is the power above the
    machine's usual idle draw while a model is reading or writing. Idle draw is the 10th percentile of the
    last day's power, so it follows the screen and background apps. On Linux only the GPUs are metered."""
    power = HW[platform]["meter"]

    def ai(active):  # Wh above idle draw while `active` is 1
        return f"sum_over_time((clamp_min({power} - {{base}}, 0) * {active})[{{range}}:{{step}}s]) * {{step}} / 3600"

    return {
        "meter": "whole Mac" if platform == MAC else "GPUs only",
        "total_query": f"sum_over_time(({power})[{{range}}:{{step}}s]) * {{step}} / 3600",
        "idle_query": IDLE,  # recorded once a minute by Prometheus
        "ai_query": ai(ACTIVE),  # the band: model runs and fine-tunes together
        "ai_run_query": ai(RUN_ACTIVE),  # Energy/token: only while a model answered
        "ai_training_query": ai(TRAIN_ACTIVE),  # Energy/trained token: only while a fine-tune trained
        "tokens_query": "sum(increase(vllm:generation_tokens_total[{range}]))",
        "trained_tokens_query": "sum(increase(vitals_training_tokens_total[{range}]))",
    }


def _tiles(platform: str) -> dict:
    hw = HW[platform]
    slow_title, slow_query, slow_names, _, _ = hw["slowdown"]
    power_title, power_query = hw["power"]
    if platform == MAC:
        mem = {  # a model run's own figures when it reports them, else the whole machine's (macmon)
            "used_query": "max(vitals_peak_memory_bytes) or max(macmon_memory_ram_used_bytes)",
            "limit_query": "max(vitals_gpu_memory_limit_bytes) or max(macmon_memory_ram_total_bytes)",
            "model_query": "max(vitals_gpu_memory_limit_bytes)",
        }
    else:
        mem = {
            "used_query": "sum(vitals_gpu_memory_used_bytes)",
            "limit_query": "sum(vitals_gpu_memory_total_bytes)",
        }
    run = [
        {"title": "Progress", "kind": "progress", "done_query": PAGES_DONE, "total_query": PAGES_TOTAL},
        {"title": "Writing now", "kind": "speed", "unit": "tok/s", "query": "max(vitals_current_generation_tps)",
         "max_query": "max(quantile_over_time(0.99, vitals_current_generation_tps[{range}]))"},
        {"title": "TTFT (median)", "kind": "zones", "unit": "s", "query": f"max({TTFT_Q.format(q=0.5)})",
         "min": 0, "max": 40, "warn": 15, "bad": 30},
        {"title": "Reading speed", "kind": "speed", "unit": "tok/s", "query": "max(vitals_last_prompt_tps)",
         "max_query": "max(quantile_over_time(0.99, vitals_last_prompt_tps[{range}]))"},
        {"title": "Context used", "kind": "bar", "unit": "%", "scale": 100, "query": f"max({CONTEXT})",
         "warn": 75, "bad": 100, "ticks": [75],
         "used_query": "max(vitals_current_context_tokens or vitals_last_context_used_tokens)",
         "limit_query": "max(vitals_context_limit_tokens)"},
        {"title": "Answers cut off", "kind": "ratio", "query": "sum(vitals_answers_cut_off_total)",
         "of_query": "sum(vitals_pages_done)", "warn": 1, "bad": 5},
        {"title": "Invalid answers", "kind": "ratio", "query": "sum(vitals_answers_invalid_total)",
         "of_query": "sum(vitals_pages_done)", "warn": 1, "bad": 5},
        {"title": "Cold start TTFT", "kind": "plain", "unit": "s", "query": "max(vitals_cold_ttft_s)",
         "note": "after loading"},
    ]  # fmt: skip
    machine = [
        {"title": "GPU mem left", "kind": "split", "unit": "bytes", "query": hw["gpu_mem_left"], **mem,
         "warn": 4e9, "bad": 2e9, "low_is_bad": True},
        {"title": "Memory free", "kind": "bar", "unit": "%", "query": "max(vitals_host_memory_free_percent)",
         "warn": 30, "bad": 15, "low_is_bad": True, "ticks": [15, 30]},
        {"title": "Swap in/out", "kind": "spark", "unit": "Bps", "query": SWAP_RATE, "warn": 10e6, "bad": 100e6},
        {"title": "GPU busy", "kind": "ring", "unit": "%", "scale": 100, "query": hw["gpu_busy"]},
        {"title": "GPU temp", "kind": "zones", "unit": "°C", "query": hw["gpu_temp"],
         "min": 30, "max": 105, "warn": 85, "bad": 95},
        {"title": slow_title, "kind": "steps", "query": slow_query, "names": slow_names},
        {"title": power_title, "kind": "speed", "unit": "W", "query": power_query,
         "max_query": f"max_over_time(({power_query})[{{range}}:1m])"},
        {"title": "Energy/token", "kind": "energy-per-token", "unit": "J/tok"},
    ]  # fmt: skip
    return {"run": run, "machine": machine}


def _training(platform: str) -> dict:
    """A fine-tune: the band (its step's phase), its own tiles, and loss against GPU temperature. The page
    shows these instead of a model run's whenever the fine-tune reported last."""
    # the last value the latest run reported, so a finished run stays readable (its final loss, its
    # verdict) and an older run's numbers never mix in
    latest = "and on(run_name, run_id) topk(1, last_over_time(vitals_training_updated_seconds[7d]))"
    t = ("max(last_over_time(vitals_training_{}[7d]) " + latest + ")").format
    tiles = [
        {"title": "Progress", "kind": "progress", "of": "steps", "done_query": t("step"),
         "total_query": t("total_steps")},
        {"title": "Train loss", "kind": "spark", "unit": "none", "query": t("loss")},
        {"title": "Val loss", "kind": "spark", "unit": "none", "query": t("val_loss"),
         "best_query": t("val_loss_best")},
        {"title": "Learning?", "kind": "steps", "query": t("verdict"), "names": training.VERDICT_NAMES},
        {"title": "Tokens/s", "kind": "speed", "unit": "tok/s", "query": t("tokens_per_second"),
         "max_query": "max(quantile_over_time(0.99, vitals_training_tokens_per_second[{range}]))"},
        {"title": "Learning rate", "kind": "plain", "unit": "none", "query": t("learning_rate")},
        {"title": "Peak memory", "kind": "plain", "unit": "bytes", "query": t("peak_memory_bytes"),
         "note": "the trainer's highest so far"},
        {"title": "Time per step", "kind": "spark", "unit": "s", "query": t("step_seconds")},
    ]  # fmt: skip
    return {
        "band": {
            # the latest run's phase (0 once it has finished), with its name
            "phase_query": f"last_over_time(vitals_training_phase[7d]) {latest}",
            "names": [k.capitalize() for k in training.PHASES],  # Idle, Step, Validate, Save
            "part_query": t("step_part"),
            "parts": ["", *training.PARTS],  # 0: none reported
            "step_query": t("step"),
            "total_query": t("total_steps"),
            "epoch_query": t("epoch"),
            "epochs_query": t("epochs"),
            "step_seconds_query": t("step_seconds"),
            # the run's span: its charts show just the run, however long or short
            "started_query": t("started_seconds"),
            "now_phase_query": t("phase"),  # one number: above 0 while the latest run is still going
            "alive_query": t("alive_seconds"),  # when it was last read: a killed run stops being read
            "updated_query": t("updated_seconds"),
        },
        "tiles": tiles,
    }


def _lead_charts(platform: str) -> dict:
    """The page's own charts, which lead their tabs: each against GPU temperature, the first thing to look
    at when a run slows down."""
    temp = {"expr": HW[platform]["gpu_temp"], "legend": "GPU °C", "unit": "°C", "axis": "right"}
    return {
        "Speed detail": {
            "title": "Writing speed against GPU temperature",
            "targets": [
                {
                    "expr": "vitals_current_generation_tps",
                    "legend": "tokens/s {{model_name}}",
                    "unit": "tok/s",
                },
                temp,
            ],
        },
        "Training": {
            "title": "Training and validation loss, against GPU temperature",
            "targets": [
                {"expr": "vitals_training_loss", "legend": "train loss {{run_name}}", "unit": "none"},
                {
                    "expr": "vitals_training_val_loss",
                    "legend": "validation loss {{run_name}}",
                    "unit": "none",
                },
                temp,
            ],
        },
    }


PAGES_DONE = "max(vitals_pages_done) + max(vitals_pages_already_answered)"  # one spelling: asked once
PAGES_TOTAL = "max(vitals_pages_total)"
TAB = {
    "Speed detail": "Speed",
    "Context and limits": "Context",
}  # a Grafana row's name, short enough for a tab
REPLACED = {"Training": "Training and validation loss"}  # Grafana's chart the page draws with temperature


def _views(platform: str) -> tuple[dict, list]:
    """(every chart by title, the tabs). Favourites first; then one tab per folded Grafana row, in its order,
    holding that row's charts, led by the page's own chart where there is one."""
    leads, charts = _lead_charts(platform), {}
    views = [{"key": "favourites", "label": "Favourites", "charts": []}]
    for row in _sections(platform):
        panels = [p for p in row["panels"] if p["title"] != REPLACED.get(row["title"])]
        panels = ([leads[row["title"]]] if row["title"] in leads else []) + panels
        charts.update({p["title"]: p for p in panels})
        label = TAB.get(row["title"], row["title"])
        view = {
            "key": label.lower(),
            "label": label,
            "row": row["title"],
            "charts": [p["title"] for p in panels],
        }
        if row["title"] == "Training":
            view["run"] = True  # its charts span the latest fine-tune, whatever the range
        views.append(view)
    return charts, views


def _favourites(platform: str, views: list) -> dict:
    """What Favourites shows until someone stars their own, four charts to fill its grid: for a model run,
    the speed chart, the model's memory, swap and the machine's memory; for a fine-tune, its loss and speed,
    its peak memory against the GPU limit, and swap."""
    by_key = {v["key"]: v["charts"] for v in views}
    swap = "Swap activity (in and out)"
    mac = platform == MAC
    gpu = "MLX GPU memory, against the GPU limit" if mac else "GPU memory (VRAM), used against total"
    machine = "Mac memory and swap in use" if mac else "Memory available (percent)"
    return {
        "inference": [by_key["speed"][0], by_key["memory"][0], swap, machine],
        "training": [*by_key["training"][:2], gpu, swap],
    }


def _target(t: dict) -> dict:
    """A chart line for the page; one that is a ceiling (queries.LIMITS) says so, and the axis ends there."""
    line = {"expr": t["expr"], "legend": t.get("legendFormat", "")}
    return {**line, "limit": True} if any(m in t["expr"] for m in LIMITS) else line


def _sections(platform: str) -> list[dict]:
    """The Grafana dashboard's folded sections, panel for panel: same titles, queries, legends and units."""
    out = []
    for row in dashboard(platform)["panels"]:
        if row["type"] != "row" or not row.get("panels"):
            continue
        panels = [
            {
                "title": p["title"],
                "unit": p["fieldConfig"]["defaults"].get("unit", "none"),
                "targets": [_target(t) for t in p["targets"]],
            }
            for p in row["panels"]
        ]
        out.append({"title": row["title"], "panels": panels})
    return out


def spec(platform: str) -> dict:
    charts, views = _views(platform)
    return {
        "platform": platform,
        "repo": REPO,
        "version": monitor.VERSION,
        "ranges": RANGES,
        "running": {
            "phase_query": "vitals_current_phase",
            "names": PHASE_NAMES,
            "done_query": PAGES_DONE,
            "total_query": PAGES_TOTAL,
            "seconds_per_page_query": "sum(rate(vllm:e2e_request_latency_seconds_sum[10m])) / "
            "sum(rate(vllm:e2e_request_latency_seconds_count[10m]))",
        },
        "energy": _energy(platform),
        "tiles": _tiles(platform),
        "training": _training(platform),
        # whichever reported last, a model run or a fine-tune, decides what the band and run tiles show
        "mode": {
            "training_query": "max(max_over_time(vitals_training_updated_seconds[7d]))",
            "run_query": "max(max_over_time(vitals_run_updated_seconds[7d]))",
        },
        # laptops: the battery's own discharge rate gives the time left at this load
        "battery": {
            "percent_query": "max(vitals_battery_percent)",
            "wh_query": "max(vitals_battery_remaining_wh)",
            "on_power_query": "max(vitals_battery_on_power)",
            "draw_query": "max(vitals_battery_draw_watts)",
        },
        "charts": charts,
        "views": views,
        "favourites": _favourites(platform, views),
    }


def serve(port: int, platform: str, prometheus: str = f"http://127.0.0.1:{monitor.PROMETHEUS}"):
    """Serve the page on 127.0.0.1:port (0 picks a free port) in a background thread."""
    described = json.dumps(spec(platform)).encode()
    files = {path: ((PAGE / name).read_bytes(), ctype) for path, (name, ctype) in FILES.items()}  # read once

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body: bytes, ctype="text/plain; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path in files:
                return self._send(200, *files[url.path])
            if url.path == "/api/spec":
                return self._send(200, described, "application/json")
            if url.path in FORWARD:
                given = urllib.parse.parse_qs(url.query)
                kept = [(k, given[k][0]) for k in FORWARD[url.path] if k in given]
                target = f"{prometheus}{url.path}?{urllib.parse.urlencode(kept)}"
                try:
                    with urllib.request.urlopen(target, timeout=30) as r:
                        return self._send(r.status, r.read(), "application/json")
                except urllib.error.HTTPError as e:  # Prometheus says why a query failed: pass it on
                    return self._send(e.code, e.read(), "application/json")
                except OSError:
                    return self._send(
                        502, b'{"status":"error","error":"Prometheus is not answering"}', "application/json"
                    )
            return self._send(404, b"not found")

        def do_POST(self):
            self._send(405, b"read-only")

        do_PUT = do_DELETE = do_PATCH = do_POST

    server = Server(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=monitor.DASHBOARD)
    p.add_argument("--platform", choices=[MAC, LINUX], default=monitor.detect_platform())
    p.add_argument("--prometheus", default=f"http://127.0.0.1:{monitor.PROMETHEUS}")
    a = p.parse_args()
    server = serve(a.port, a.platform, a.prometheus)
    run_until_interrupt(server)


if __name__ == "__main__":
    main()
