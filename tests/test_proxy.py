import http.server
import json
import threading
import time
import urllib.request

from inference_vitals import proxy


def _sse(*objs):
    return b"".join(b"data: " + json.dumps(o).encode() + b"\n\n" for o in objs) + b"data: [DONE]\n\n"


def _chunk(text, finish=None):
    return {"choices": [{"delta": {"content": text}, "finish_reason": finish}]}


def test_openai_stream_with_usage_gives_exact_numbers():
    w = proxy.Watcher(started=100.0)
    w.feed(_sse(_chunk("Hel"))[:20], 101.0)  # a line split across reads
    w.feed(_sse(_chunk("Hel"))[20:-14], 101.0)
    w.feed(
        _sse(_chunk("lo", "length"), {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 2}}),
        102.0,
    )
    r = w.finish(103.0)
    assert r["ttft_s"] == 1.0 and r["seconds"] == 3.0
    assert (r["prompt_tokens"], r["generation_tokens"], r["tokens_exact"]) == (12, 2, True)
    assert r["finish_reason"] == "length"


def test_openai_stream_without_usage_counts_chunks():
    w = proxy.Watcher(started=0.0)
    w.feed(_sse(_chunk("a"), _chunk("b"), _chunk("c"), _chunk("", "stop")), 2.0)
    r = w.finish(4.0)
    assert r["generation_tokens"] == 3 and r["tokens_exact"] is False and r["finish_reason"] == "stop"
    assert r["generation_tps"] == 1.5  # 3 tokens over the 2 s after the first


def test_ollama_native_stream_uses_its_own_timings():
    lines = [
        {"message": {"content": "Hi"}, "done": False},
        {"message": {"content": "!"}, "done": False},
        {
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 40,
            "prompt_eval_duration": 200_000_000,
            "eval_count": 2,
            "eval_duration": 100_000_000,
            "load_duration": 5_000_000_000,
        },
    ]
    w = proxy.Watcher(started=0.0)
    w.feed(b"".join(json.dumps(x).encode() + b"\n" for x in lines), 0.5)
    r = w.finish(1.0)
    assert (r["prompt_tokens"], r["generation_tokens"]) == (40, 2)
    assert r["prompt_tps"] == 200.0 and r["generation_tps"] == 20.0
    assert r["warm"] is False  # Ollama loaded the model for this request


def test_llama_cpp_timings_are_used():
    w = proxy.Watcher(started=0.0)
    final = {
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "timings": {
            "prompt_n": 50,
            "prompt_per_second": 500.0,
            "predicted_n": 20,
            "predicted_per_second": 25.0,
        },
    }
    w.feed(_sse(_chunk("x"), final), 1.0)
    r = w.finish(2.0)
    assert (r["prompt_tokens"], r["prompt_tps"], r["generation_tokens"], r["generation_tps"]) == (
        50,
        500.0,
        20,
        25.0,
    )


def test_non_streamed_reply_split_across_reads():
    body = json.dumps(
        {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 1},
        }
    ).encode()
    w = proxy.Watcher(started=0.0)
    w.feed(body[:15], 1.0)
    w.feed(body[15:], 1.0)
    r = w.finish(1.0)
    assert (r["prompt_tokens"], r["generation_tokens"], r["finish_reason"]) == (9, 1, "stop")


def test_nothing_from_the_content_is_kept():
    w = proxy.Watcher(started=0.0)
    w.feed(_sse(_chunk("SECRET")), 1.0)
    assert "SECRET" not in json.dumps(w.finish(2.0))


class _FakeEngine(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b'{"data": [{"id": "fake"}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for part in [_chunk("a"), _chunk("b"), _chunk("", "stop")]:
            data = b"data: " + json.dumps(part).encode() + b"\n\n"
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()
            time.sleep(0.05)
        self.wfile.write(b"e\r\ndata: [DONE]\n\n\r\n0\r\n\r\n")


def test_round_trip_through_the_proxy():
    engine = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeEngine)
    threading.Thread(target=engine.serve_forever, daemon=True).start()
    board = proxy.Board()
    server = proxy.serve(f"http://127.0.0.1:{engine.server_address[1]}", 0, board)
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models") as r:
            assert json.loads(r.read()) == {"data": [{"id": "fake"}]}
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            method="POST",
            data=json.dumps({"model": "fake-7b", "stream": True, "messages": []}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as r:
            body = r.read()
        assert body.count(b"data: ") == 4 and body.endswith(b"data: [DONE]\n\n")
        text = board.render()
        assert 'vllm:generation_tokens_total{model_name="fake-7b"} 2' in text
        assert 'vitals_pages_done{model_name="fake-7b"} 1' in text
    finally:
        server.shutdown()
        engine.shutdown()


def test_a_model_given_as_a_file_path_is_labelled_by_its_folder_name_only():
    assert proxy.model_label("/Users/someone/models/qwen3-8b-4bit/") == "qwen3-8b-4bit"
    assert proxy.model_label("~/models/qwen3-8b") == "qwen3-8b"
    assert proxy.model_label("mlx-community/Qwen3-8B-4bit") == "mlx-community/Qwen3-8B-4bit"
    assert proxy.model_label(None) == "unknown"
