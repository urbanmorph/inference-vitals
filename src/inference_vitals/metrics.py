"""Publish what a local model is doing, for Prometheus: a /metrics page in Prometheus text format on
127.0.0.1, using vLLM's metric names where one exists (so dashboards built for vLLM can read it), plus
/metrics.json.

    from inference_vitals.metrics import Metrics, serve
    live = Metrics(total=len(items), kv_per_token=0, labels={"model_name": "my-model"})
    serve(live, 8400)
    for item in items:
        live.started(item)
        ...                                   # call live.progress(tokens, seconds) as tokens arrive
        live.finished({"ttft_s": ..., "generation_tps": ..., "prompt_tokens": ..., ...})

Tokens count as they are written, as in vLLM, so speeds and energy per token stay live during a long
answer; the context in use grows with them when `progress` is given the prompt's size.

Only numbers are kept. A record's text, answer and id never reach the page: they can hold private
content, and the page is readable by any program on this Mac.

While an item is being answered the page also shows what is happening now: reading the prompt (phase 1)
or writing the answer (phase 2), with tokens so far and the current writing speed.
"""

import http.server
import json
import threading
import time

NUMBERS = (
    "ttft_s",
    "seconds",
    "prompt_tokens",
    "cached_tokens",
    "generation_tokens",
    "prompt_tps",
    "generation_tps",
    "active_memory_gb",
    "peak_memory_gb",
    "cache_memory_gb",
    "thermal_warning",
    "cpu_speed_limit",
    "context_used_tokens",
    "context_limit_tokens",
    "gpu_memory_limit_gb",
    "load_s",
)
LAST = (
    "ttft_s",
    "prompt_tps",
    "generation_tps",
    "prompt_tokens",
    "cached_tokens",
    "generation_tokens",
    "thermal_warning",
    "cpu_speed_limit",
    "context_used_tokens",
)
BUCKETS = {
    "vllm:time_to_first_token_seconds": (0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 40.0, 80.0),
    "vllm:e2e_request_latency_seconds": (5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 320.0),
    "vllm:time_per_output_token_seconds": (0.02, 0.05, 0.1, 0.2, 0.5, 1.0),
}
IDLE, READING, WRITING = 0, 1, 2
DEFAULT_PORT = 8400  # what the dashboard's Prometheus reads; 127.0.0.1 only


def kv_bytes_per_token(config: dict) -> int:
    """Bytes of KV cache each token adds: 2 (key, value) x layers that keep a cache x KV heads x head size
    x 2 bytes (16-bit cache). Linear-attention layers keep a fixed-size state instead, so are not counted."""
    c = config.get("text_config", config)
    types = c.get("layer_types")
    layers = sum(t == "full_attention" for t in types) if types else c["num_hidden_layers"]
    head_dim = c.get("head_dim") or c["hidden_size"] // c["num_attention_heads"]
    return 2 * layers * c.get("num_key_value_heads", c.get("num_attention_heads")) * head_dim * 2


class Metrics:
    def __init__(
        self,
        total: int | None,
        kv_per_token: int,
        labels: dict | None = None,
        context_limit: int | None = None,
    ):
        self.total, self.kv_per_token = total, kv_per_token
        self.labels = labels or {}
        self.lock = threading.Lock()
        self.done = self.already = self.running = self.prompt_tokens = self.generation_tokens = 0
        self.cut_off = self.invalid = self.over_context = self.failed = 0
        self.phase, self.now_tokens, self.now_tps, self.now_prompt = IDLE, 0, 0.0, None
        self.last: dict = {}
        self.sticky: dict = {}  # values that hold for the whole run: limits, load time, cold TTFT
        if context_limit:
            self.sticky["context_limit_tokens"] = context_limit
        self.hist = {name: {"counts": [0] * len(b), "sum": 0.0, "n": 0} for name, b in BUCKETS.items()}
        self.updated = (
            None  # wall time of the last change: the page shows whichever run, model or training, is newer
        )

    def cached(self, _item):
        with self.lock:
            self.already += 1
            self.updated = time.time()

    def started(self, _item):
        with self.lock:
            self.running += 1
            self.phase, self.now_tokens, self.now_tps, self.now_prompt = READING, 0, 0.0, None
            self.updated = time.time()

    def progress(self, tokens: int, writing_seconds: float, prompt_tokens: int | None = None):
        """Called as tokens arrive: `tokens` written so far, `writing_seconds` since the first one, and the
        prompt's size if known."""
        with self.lock:
            self.phase, self.now_tokens, self.now_prompt = WRITING, tokens, prompt_tokens
            self.updated = time.time()
            # over at least a second: the first tokens arrive within milliseconds, which would read as
            # hundreds of thousands of tokens a second
            self.now_tps = round(tokens / max(writing_seconds, 1.0), 1)

    def _observe(self, name, value):
        h = self.hist[name]
        for i, edge in enumerate(BUCKETS[name]):
            h["counts"][i] += value <= edge
        h["sum"] += value
        h["n"] += 1

    def finished(self, record: dict):
        nums = {k: record[k] for k in NUMBERS if isinstance(record.get(k), (int, float))}
        with self.lock:
            self.running = max(0, self.running - 1)
            self.done += 1
            self.updated = time.time()
            self.prompt_tokens += nums.get("prompt_tokens", 0)
            # the final count replaces the live one, but a counter never goes down
            self.generation_tokens += max(nums.get("generation_tokens", 0), self._writing())
            self.phase, self.now_tokens = IDLE, 0
            self.cut_off += record.get("finish_reason") == "length"
            self.invalid += bool(record.get("parse_error"))
            self.failed += bool(record.get("failed"))
            self.over_context += bool(record.get("over_context"))
            self.last = nums
            for k in ("context_limit_tokens", "gpu_memory_limit_gb", "load_s"):
                if k in nums:
                    self.sticky[k] = nums[k]
            if (
                record.get("warm") is False
            ):  # warm-up after loading: shown alone, kept out of the distribution
                self.sticky["cold_ttft_s"] = nums.get("ttft_s")
            elif "ttft_s" in nums:
                self._observe("vllm:time_to_first_token_seconds", nums["ttft_s"])
            if "seconds" in nums:
                self._observe("vllm:e2e_request_latency_seconds", nums["seconds"])
            if nums.get("generation_tps"):
                self._observe("vllm:time_per_output_token_seconds", 1 / nums["generation_tps"])

    def _writing(self) -> int:
        return self.now_tokens if self.phase == WRITING else 0

    def _kv_estimate(self):
        return (self.last.get("prompt_tokens", 0) + self.last.get("generation_tokens", 0)) * self.kv_per_token

    def _line(self, name, value, **extra) -> str:
        labels = {**self.labels, **extra}
        tags = ",".join(f'{k}="{v}"' for k, v in labels.items())
        return f"{name}{{{tags}}} {value}" if tags else f"{name} {value}"

    def render(self) -> str:
        with self.lock:
            out = [
                ("vllm:prompt_tokens_total", self.prompt_tokens),
                ("vllm:generation_tokens_total", self.generation_tokens + self._writing()),
                ("vllm:num_requests_running", self.running),
                ("vitals_answers_cut_off_total", self.cut_off),
                ("vitals_answers_invalid_total", self.invalid),
                ("vitals_over_context_total", self.over_context),
                ("vitals_requests_failed_total", self.failed),
            ]
            lines = [self._line(n, v) for n, v in out]
            for name, edges in BUCKETS.items():
                h = self.hist[name]
                lines += [self._line(f"{name}_bucket", c, le=e) for e, c in zip(edges, h["counts"])]
                lines += [
                    self._line(f"{name}_bucket", h["n"], le="+Inf"),
                    self._line(f"{name}_sum", f"{h['sum']:.4f}"),
                    self._line(f"{name}_count", h["n"]),
                ]
            gauges = [
                ("vitals_pages_done", self.done),
                ("vitals_pages_already_answered", self.already),
                ("vitals_kv_cache_estimate_bytes", self._kv_estimate()),
                ("vitals_current_phase", self.phase),
                ("vitals_current_generation_tokens", self.now_tokens),
                ("vitals_current_generation_tps", self.now_tps),
            ]
            if self.phase == WRITING and self.now_prompt is not None:
                gauges.append(("vitals_current_context_tokens", self.now_prompt + self.now_tokens))
            if self.total is not None:  # unknown when a proxy just watches whatever comes through
                gauges.append(("vitals_pages_total", self.total))
            s = self.sticky
            if s.get("cold_ttft_s") is not None:
                gauges.append(("vitals_cold_ttft_s", s["cold_ttft_s"]))
            if "context_limit_tokens" in s:
                gauges.append(("vitals_context_limit_tokens", s["context_limit_tokens"]))
            if "gpu_memory_limit_gb" in s:
                gauges.append(("vitals_gpu_memory_limit_bytes", round(s["gpu_memory_limit_gb"] * 1e9)))
            if "load_s" in s:
                gauges.append(("vitals_model_load_seconds", s["load_s"]))
            gauges += [(f"vitals_last_{k}", self.last[k]) for k in LAST if k in self.last]
            if self.updated is not None:
                gauges.append(("vitals_run_updated_seconds", self.updated))
            for k in ("active_memory_gb", "peak_memory_gb", "cache_memory_gb"):
                if k in self.last:
                    gauges.append((f"vitals_{k.removesuffix('_gb')}_bytes", round(self.last[k] * 1e9)))
            lines += [self._line(n, v) for n, v in gauges]
            return "\n".join(lines) + "\n"

    def snapshot(self) -> str:
        with self.lock:
            return json.dumps(
                {
                    "pages_done": self.done,
                    "pages_total": self.total,
                    "pages_already_answered": self.already,
                    "running": self.running,
                    "phase": self.phase,
                    "current_generation_tokens": self.now_tokens,
                    "current_generation_tps": self.now_tps,
                    "prompt_tokens_total": self.prompt_tokens,
                    "generation_tokens_total": self.generation_tokens + self._writing(),
                    "answers_cut_off": self.cut_off,
                    "answers_invalid": self.invalid,
                    "over_context": self.over_context,
                    "kv_cache_estimate_bytes": self._kv_estimate(),
                    "last": self.last,
                    **self.sticky,
                }
            )


def serve(source, port: int):
    """Serve source.render() at /metrics and source.snapshot() at /metrics.json on 127.0.0.1:port
    (0 picks a free port), in a background thread."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            pages = {
                "/metrics": (source.render, "text/plain; version=0.0.4"),
                "/metrics.json": (source.snapshot, "application/json"),
            }
            if self.path not in pages:
                self.send_response(404)
                self.end_headers()
                return
            make, ctype = pages[self.path]
            body = make().encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
