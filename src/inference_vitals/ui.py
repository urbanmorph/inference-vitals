"""inference-vitals' own page: the dashboard without Grafana, served on 127.0.0.1 only.

The page is plain HTML, CSS and JavaScript from this package (nothing is fetched from the internet). It
reads Prometheus through two read-only routes, /api/v1/query and /api/v1/query_range, passed through with
only their own parameters; nothing else reaches Prometheus and nothing can be written.

What the page shows comes from `spec(platform)`: the tiles (each with the gauge that suits its number),
the energy used in the chosen time range and how much of it was the model's, the main chart, and the
folded sections, which are the Grafana dashboard's own detail panels so the two never drift apart.

  python -m inference_vitals.ui --port 3000 --platform mac
"""

import argparse
import http.server
import json
import pathlib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from inference_vitals import monitor
from inference_vitals.monitor import CONTEXT, HW, LINUX, MAC, REPO, SWAP_RATE, TTFT_Q

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
ACTIVE = "(max(vitals_current_phase) > bool 0)"  # 1 while a model is reading or writing, else 0


def _energy(platform: str) -> dict:
    """Energy over the chosen range: all of it, and the model's share, which is the power above the
    machine's usual idle draw while a model is reading or writing. Idle draw is the 10th percentile of the
    last day's power, so it follows the screen and background apps. On Linux only the GPUs are metered."""
    power = "max(macmon_sys_power_watts)" if platform == MAC else "sum(vitals_gpu_power_watts)"
    return {
        "meter": "whole Mac" if platform == MAC else "GPUs only",
        "power_query": power,
        "total_query": f"sum_over_time(({power})[{{range}}:{{step}}s]) * {{step}} / 3600",
        "idle_query": f"quantile_over_time(0.1, ({power})[24h:1m])",
        "ai_query": f"sum_over_time((clamp_min({power} - {{base}}, 0) * {ACTIVE})[{{range}}:{{step}}s]) * {{step}} / 3600",
        "tokens_query": "sum(increase(vllm:generation_tokens_total[{range}]))",
    }


def _tiles(platform: str) -> dict:
    hw = HW[platform]
    slow_title, slow_query, slow_names, _, _ = hw["slowdown"]
    power_title, power_query = hw["power"]
    if platform == MAC:
        mem = {
            "used_query": "max(vitals_peak_memory_bytes)",
            "limit_query": "max(vitals_gpu_memory_limit_bytes)",
        }
    else:
        mem = {
            "used_query": "sum(vitals_gpu_memory_used_bytes)",
            "limit_query": "sum(vitals_gpu_memory_total_bytes)",
        }
    done = "(max(vitals_pages_done) + max(vitals_pages_already_answered))"
    run = [
        {"title": "Progress", "kind": "progress", "query": f"{done} / max(vitals_pages_total)",
         "done_query": done, "total_query": "max(vitals_pages_total)"},
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
         "note": "first answer after loading"},
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


def _sections(platform: str) -> list[dict]:
    """The Grafana dashboard's folded sections, panel for panel: same titles, queries, legends and units."""
    out = []
    for row in monitor.dashboard(platform)["panels"]:
        if row["type"] != "row" or not row.get("panels"):
            continue
        panels = [
            {
                "title": p["title"],
                "unit": p["fieldConfig"]["defaults"].get("unit", "none"),
                "targets": [{"expr": t["expr"], "legend": t.get("legendFormat", "")} for t in p["targets"]],
            }
            for p in row["panels"]
        ]
        out.append({"title": row["title"], "panels": panels})
    return out


def spec(platform: str) -> dict:
    hw = HW[platform]
    return {
        "platform": platform,
        "repo": REPO,
        "ranges": RANGES,
        "running": {
            "phase_query": "vitals_current_phase",
            "names": ["Idle", "Reading (prefill)", "Writing (decode)"],
            "done_query": "max(vitals_pages_done) + max(vitals_pages_already_answered)",
            "total_query": "max(vitals_pages_total)",
            "seconds_per_page_query": "sum(rate(vllm:e2e_request_latency_seconds_sum[10m])) / "
            "sum(rate(vllm:e2e_request_latency_seconds_count[10m]))",
        },
        "energy": _energy(platform),
        "tiles": _tiles(platform),
        "main": {
            "title": "Writing speed against GPU temperature",
            "targets": [
                {
                    "expr": "vitals_current_generation_tps",
                    "legend": "tokens/s {{model_name}}",
                    "unit": "tok/s",
                },
                {"expr": hw["gpu_temp"], "legend": "GPU °C", "unit": "°C", "axis": "right"},
            ],
        },
        "sections": _sections(platform),
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

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=monitor.DASHBOARD)
    p.add_argument("--platform", choices=[MAC, LINUX], default=monitor.detect_platform())
    p.add_argument("--prometheus", default=f"http://127.0.0.1:{monitor.PROMETHEUS}")
    a = p.parse_args()
    server = serve(a.port, a.platform, a.prometheus)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
