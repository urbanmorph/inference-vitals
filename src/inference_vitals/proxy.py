"""Put inference-vitals in front of any local engine with an HTTP API.

  inference-vitals proxy --to http://127.0.0.1:11434        (Ollama; LM Studio is :1234, llama.cpp :8080)

Point your app at the proxy (127.0.0.1:8401 by default) instead of the engine. Every request is passed
through unchanged and the reply streams back unchanged; on the way, the proxy times it and counts its
tokens, and publishes the numbers on 127.0.0.1:8400/metrics, where the dashboard reads them, one line per
model (from the request's "model" field).

Understood: OpenAI-style /v1/chat/completions and /v1/completions (Ollama, LM Studio, llama.cpp,
mlx-lm and mlx-vlm servers, Jan, LocalAI, vLLM) and Ollama's own /api/chat and /api/generate. When the
engine reports token counts and timings (OpenAI "usage", llama.cpp "timings", Ollama's counts and
durations), those exact numbers are used; otherwise tokens are counted as they stream in.

Only numbers are kept: no prompt, answer or request text is stored, logged or published.
"""

import argparse
import http.client
import http.server
import json
import threading
import time
import urllib.parse

from inference_vitals.metrics import DEFAULT_PORT, Metrics, Server, run_until_interrupt
from inference_vitals.metrics import serve as serve_metrics

PROXY_PORT = 8401
WATCHED = ("/chat/completions", "/completions", "/api/chat", "/api/generate")
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}
NS = 1e9


class Watcher:
    """Reads one reply as it streams past and works out its numbers. Keeps counts and times, never text."""

    def __init__(self, started: float):
        self.started, self.first, self.counted = started, None, 0
        self.buffer = b""
        self.exact: dict = {}
        self.finish_reason = None
        self.load_s = None

    def _content(self, obj) -> str:
        if not isinstance(obj, dict):
            return ""
        choice = (obj.get("choices") or [{}])[0] if isinstance(obj.get("choices"), list) else {}
        delta = choice.get("delta") or choice.get("message") or {}
        parts = [
            delta.get("content"),
            delta.get("reasoning_content"),
            choice.get("text"),
            (obj.get("message") or {}).get("content"),
            obj.get("response"),
        ]
        if choice.get("finish_reason"):
            self.finish_reason = choice["finish_reason"]
        return "".join(p for p in parts if isinstance(p, str))

    def _numbers(self, obj):
        if not isinstance(obj, dict):
            return
        usage = obj.get("usage") or {}
        if usage.get("prompt_tokens") is not None:
            self.exact["prompt_tokens"] = usage["prompt_tokens"]
        if usage.get("completion_tokens") is not None:
            self.exact["generation_tokens"] = usage["completion_tokens"]
        t = obj.get("timings") or {}  # llama.cpp
        for src, dst in (
            ("prompt_n", "prompt_tokens"),
            ("predicted_n", "generation_tokens"),
            ("prompt_per_second", "prompt_tps"),
            ("predicted_per_second", "generation_tps"),
        ):
            if t.get(src) is not None:
                self.exact[dst] = round(t[src], 1) if dst.endswith("tps") else t[src]
        if obj.get("done") is True:  # Ollama's last line
            if obj.get("done_reason"):
                self.finish_reason = obj["done_reason"]
            if obj.get("prompt_eval_count") is not None:
                self.exact["prompt_tokens"] = obj["prompt_eval_count"]
            if obj.get("eval_count") is not None:
                self.exact["generation_tokens"] = obj["eval_count"]
            if obj.get("prompt_eval_duration"):
                self.exact["prompt_tps"] = round(
                    obj.get("prompt_eval_count", 0) / (obj["prompt_eval_duration"] / NS), 1
                )
            if obj.get("eval_duration"):
                self.exact["generation_tps"] = round(
                    obj.get("eval_count", 0) / (obj["eval_duration"] / NS), 1
                )
            if obj.get("load_duration") is not None:
                self.load_s = obj["load_duration"] / NS

    def _take(self, obj, now) -> int:
        self._numbers(obj)
        if self._content(obj):
            if self.first is None:
                self.first = now
            self.counted += 1
            return 1
        return 0

    def _line(self, line: bytes, now) -> int:
        line = line.strip()
        if line.startswith(b"data:"):
            line = line[5:].strip()
        if not line or line == b"[DONE]" or line.startswith((b"event:", b":")):
            return 0
        try:
            return self._take(json.loads(line), now)
        except ValueError:
            return 0

    def feed(self, data: bytes, now: float) -> int:
        """Take the next piece of the reply; returns how many new tokens it held."""
        self.buffer += data
        *lines, self.buffer = self.buffer.split(b"\n")
        return sum(self._line(line, now) for line in lines)

    def finish(self, now: float) -> dict:
        if self.buffer.strip():  # a reply that was not streamed: one JSON body, maybe without a newline
            self._line(self.buffer, now)
            self.buffer = b""
        e = self.exact
        gen = e.get("generation_tokens", self.counted)
        writing = now - self.first if self.first is not None else 0.0
        counted_tps = round(self.counted / writing, 1) if writing > 0 else None  # as the live gauge counts it
        record = {
            "seconds": round(now - self.started, 3),
            "ttft_s": round(self.first - self.started, 3) if self.first is not None else None,
            "prompt_tokens": e.get("prompt_tokens"),
            "generation_tokens": gen,
            "prompt_tps": e.get("prompt_tps"),
            "generation_tps": e.get("generation_tps", counted_tps),
            "tokens_exact": "generation_tokens" in e,
            "finish_reason": self.finish_reason,
        }
        if record["prompt_tokens"] is not None:
            record["context_used_tokens"] = record["prompt_tokens"] + gen
        if self.load_s is not None:
            record["warm"] = self.load_s < 1.0
            record["load_s"] = round(self.load_s, 2)
        return {k: v for k, v in record.items() if v is not None}


def model_label(model) -> str:
    """A model given as a file path is labelled by its folder name, so home-folder paths stay off the page."""
    if not model:
        return "unknown"
    model = str(model)
    if model.startswith(("/", "~", ".")):
        return model.rstrip("/").rsplit("/", 1)[-1] or "unknown"
    return model


class Board:
    """One Metrics per model seen, published together on one page."""

    def __init__(self):
        self.lock = threading.Lock()
        self.models: dict[str, Metrics] = {}

    def get(self, model: str) -> Metrics:
        with self.lock:
            if model not in self.models:
                self.models[model] = Metrics(total=None, kv_per_token=0, labels={"model_name": model})
            return self.models[model]

    def render(self) -> str:
        with self.lock:
            boards = list(self.models.values())
        return "".join(m.render() for m in boards)

    def snapshot(self) -> str:
        with self.lock:
            boards = dict(self.models)
        return json.dumps({name: json.loads(m.snapshot()) for name, m in boards.items()})


def serve(target: str, port: int, board: Board):
    """Pass everything on 127.0.0.1:port through to `target` (http://host:port), watching model replies."""
    up = urllib.parse.urlsplit(target)
    if up.scheme != "http":
        raise ValueError("the engine address must start with http:// (local engines only)")

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # request lines can name models and paths; keep the terminal quiet
            pass

        def _forward(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            watched = self.command == "POST" and self.path.split("?")[0].endswith(WATCHED)
            model = None
            if watched:
                try:
                    model = model_label(json.loads(body or b"{}").get("model"))
                except (ValueError, AttributeError):
                    watched = False
            conn = http.client.HTTPConnection(up.hostname, up.port or 80, timeout=600)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
            started = time.monotonic()
            live = board.get(model) if watched else None
            if live:
                live.started({})
            try:
                conn.request(
                    self.command, (up.path.rstrip("/") + self.path) or "/", body=body or None, headers=headers
                )
                resp = conn.getresponse()
            except OSError as e:
                if live:
                    live.finished({"failed": "engine unreachable"})
                self.send_error(502, f"engine at {target} did not answer: {e.__class__.__name__}")
                return
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in HOP_BY_HOP:
                    self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            watcher = Watcher(started) if live and resp.status == 200 else None
            recorded = False

            def record():  # before the closing chunk, so a client that has the whole reply sees its numbers
                nonlocal recorded
                if live and not recorded:
                    recorded = True
                    ended = time.monotonic()
                    live.finished(watcher.finish(ended) if watcher else {"failed": f"HTTP {resp.status}"})

            try:
                while chunk := resp.read1(65536):
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                    if watcher and watcher.feed(chunk, time.monotonic()) and watcher.first is not None:
                        live.progress(watcher.counted, time.monotonic() - watcher.first)
                record()
                self.wfile.write(b"0\r\n\r\n")
            finally:
                conn.close()
                record()  # the client went away mid-reply: still count the request

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _forward

        def do_HEAD(self):
            self.send_error(405)

    server = Server(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="inference-vitals proxy",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--to", required=True, help="the engine's address, e.g. http://127.0.0.1:11434")
    p.add_argument(
        "--port", type=int, default=PROXY_PORT, help=f"where your app connects (default {PROXY_PORT})"
    )
    p.add_argument("--metrics-port", type=int, default=DEFAULT_PORT, help="where the dashboard reads numbers")
    args = p.parse_args(argv)
    board = Board()
    numbers = serve_metrics(board, args.metrics_port)
    server = serve(args.to, args.port, board)
    print(
        f"Proxy: http://127.0.0.1:{args.port} -> {args.to}  (point your app here)\n"
        f"Numbers: http://127.0.0.1:{args.metrics_port}/metrics  (Ctrl-C to stop)",
        flush=True,
    )
    run_until_interrupt(server, numbers)
