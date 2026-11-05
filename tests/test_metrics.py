import json
import urllib.error
import urllib.request

from inference_vitals import metrics

QWEN38 = {
    "text_config": {
        "num_hidden_layers": 64,
        "num_key_value_heads": 4,
        "head_dim": 256,
        "layer_types": ["linear_attention"] * 3 * 16 + ["full_attention"] * 16,
    }
}
LLAMA = {"num_hidden_layers": 32, "num_attention_heads": 32, "num_key_value_heads": 8, "hidden_size": 4096}


def test_kv_bytes_per_token_counts_only_layers_that_keep_a_cache():
    assert metrics.kv_bytes_per_token(QWEN38) == 64 * 1024
    assert metrics.kv_bytes_per_token(LLAMA) == 2 * 32 * 8 * 128 * 2


def _record(i, ttft, prompt=3000, gen=200):
    return {
        "id": f"p{i}",
        "text": "SECRET PAGE TEXT",
        "answer": {"vendor": "SECRET VENDOR"},
        "ttft_s": ttft,
        "seconds": ttft + 10,
        "prompt_tokens": prompt,
        "generation_tokens": gen,
        "prompt_tps": 400.0,
        "generation_tps": 16.0,
        "active_memory_gb": 17.0,
        "peak_memory_gb": 19.1,
        "cache_memory_gb": 1.0,
    }


def test_metrics_page_has_counters_histograms_and_gauges_but_no_content():
    m = metrics.Metrics(total=10, kv_per_token=64 * 1024)
    m.cached({"id": "p0"})
    m.started({"id": "p1"})
    m.finished(_record(1, 0.8))
    m.started({"id": "p2"})
    m.finished(_record(2, 3.0, prompt=5000))
    text = m.render()
    assert "vllm:prompt_tokens_total 8000" in text
    assert "vllm:generation_tokens_total 400" in text
    assert 'vllm:time_to_first_token_seconds_bucket{le="1.0"} 1' in text
    assert 'vllm:time_to_first_token_seconds_bucket{le="+Inf"} 2' in text
    assert "vllm:time_to_first_token_seconds_count 2" in text
    assert "vllm:num_requests_running 0" in text
    assert "vitals_pages_done 2" in text and "vitals_pages_total 10" in text
    assert "vitals_pages_already_answered 1" in text
    assert "vitals_peak_memory_bytes 19100000000" in text
    assert "vitals_kv_cache_estimate_bytes 340787200" in text  # (5000 + 200) tokens x 64 KiB
    assert "SECRET" not in text and "p2" not in text


def test_metrics_server_serves_only_metrics_on_localhost():
    m = metrics.Metrics(total=1, kv_per_token=0)
    server = metrics.serve(m, 0)
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics") as r:
            assert r.status == 200 and "text/plain" in r.headers["Content-Type"]
            assert b"vitals_pages_total 1" in r.read()
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/")
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()


def test_json_snapshot_for_dashboards():
    m = metrics.Metrics(total=2, kv_per_token=1)
    m.finished(_record(1, 1.5))
    snap = json.loads(m.snapshot())
    assert snap["pages_done"] == 1 and snap["last"]["ttft_s"] == 1.5 and "text" not in snap["last"]


def test_cold_first_page_is_kept_out_of_the_ttft_distribution():
    m = metrics.Metrics(total=3, kv_per_token=0)
    m.finished({**_record(1, 9.9), "warm": False})
    m.finished({**_record(2, 1.2), "warm": True})
    text = m.render()
    assert "vllm:time_to_first_token_seconds_count 1" in text
    assert "vitals_cold_ttft_s 9.9" in text


def test_cached_prompt_tokens_are_shown():
    m = metrics.Metrics(total=1, kv_per_token=0)
    m.finished({**_record(1, 1.0), "cached_tokens": 1800})
    assert "vitals_last_cached_tokens 1800" in m.render()


def test_every_line_carries_the_model_so_two_runs_can_be_compared():
    m = metrics.Metrics(total=1, kv_per_token=0, labels={"model_name": "qwen3.8-27b-4bit"})
    m.finished({**_record(1, 1.0), "warm": True})
    text = m.render()
    assert 'vllm:prompt_tokens_total{model_name="qwen3.8-27b-4bit"} 3000' in text
    assert 'vllm:time_to_first_token_seconds_bucket{model_name="qwen3.8-27b-4bit",le="1.0"} 1' in text
    assert all('model_name="qwen3.8-27b-4bit"' in line for line in text.splitlines())


def test_thermal_state_of_the_last_page_is_shown():
    m = metrics.Metrics(total=1, kv_per_token=0)
    m.finished({**_record(1, 1.0), "thermal_warning": 2, "cpu_speed_limit": 80})
    text = m.render()
    assert "vitals_last_thermal_warning 2" in text and "vitals_last_cpu_speed_limit 80" in text


def test_progress_while_a_page_is_being_written():
    m = metrics.Metrics(total=1, kv_per_token=0)
    m.started({"id": "p1"})
    assert "vitals_current_phase 1" in m.render()  # reading the prompt
    m.progress(tokens=50, writing_seconds=2.0)
    text = m.render()
    assert "vitals_current_phase 2" in text
    assert "vitals_current_generation_tokens 50" in text and "vitals_current_generation_tps 25.0" in text
    m.finished(_record(1, 1.0))
    assert "vitals_current_phase 0" in m.render()


def test_cut_off_invalid_and_over_context_answers_are_counted():
    m = metrics.Metrics(total=3, kv_per_token=0)
    m.finished({**_record(1, 1.0), "finish_reason": "length", "parse_error": "invalid JSON"})
    m.finished(
        {
            **_record(2, 1.0),
            "finish_reason": "stop",
            "parse_error": None,
            "over_context": True,
            "context_used_tokens": 33000,
            "context_limit_tokens": 32768,
        }
    )
    text = m.render()
    assert "vitals_answers_cut_off_total 1" in text
    assert "vitals_answers_invalid_total 1" in text
    assert "vitals_over_context_total 1" in text
    assert "vitals_last_context_used_tokens 33000" in text and "vitals_context_limit_tokens 32768" in text


def test_gpu_memory_limit_and_load_time_are_shown():
    m = metrics.Metrics(total=1, kv_per_token=0)
    m.finished({**_record(1, 9.0), "warm": False, "gpu_memory_limit_gb": 40.2, "load_s": 12.5})
    text = m.render()
    assert "vitals_gpu_memory_limit_bytes 40200000000" in text and "vitals_model_load_seconds 12.5" in text


def test_total_is_optional_when_the_number_of_items_is_unknown():
    m = metrics.Metrics(total=None, kv_per_token=0)
    assert "vitals_pages_total" not in m.render()


def test_a_refused_request_is_counted_as_failed_not_as_an_invalid_answer():
    m = metrics.Metrics(total=None, kv_per_token=0)
    m.finished({"failed": "HTTP 400"})
    text = m.render()
    assert "vitals_requests_failed_total 1" in text and "vitals_answers_invalid_total 0" in text
