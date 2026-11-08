"""Shared by the tests: every metric name inference-vitals and its exporters can publish, per platform."""

from inference_vitals import gpustat, metrics, queries, sysstat, training


def known_metric_names():
    m = metrics.Metrics(total=1, kv_per_token=1, labels={"model_name": "x"})
    m.started({})
    m.progress(tokens=1, writing_seconds=1.0, prompt_tokens=1)
    writing = m.render()  # some numbers exist only while a page is being written
    m.finished(
        {
            "ttft_s": 1.0,
            "seconds": 2.0,
            "prompt_tokens": 1,
            "cached_tokens": 0,
            "generation_tokens": 1,
            "prompt_tps": 1.0,
            "generation_tps": 1.0,
            "active_memory_gb": 1.0,
            "peak_memory_gb": 1.0,
            "cache_memory_gb": 1.0,
            "warm": False,
            "thermal_warning": 0,
            "cpu_speed_limit": 100,
            "finish_reason": "length",
            "parse_error": "x",
            "over_context": True,
            "context_used_tokens": 1,
            "context_limit_tokens": 1,
            "gpu_memory_limit_gb": 1.0,
            "load_s": 1.0,
        }
    )
    run = training.Training("t", total_steps=2, epochs=1)  # a fine-tune that has done everything once
    run.step(
        1, loss=1.0, learning_rate=1e-4, tokens_per_second=1.0, peak_memory_gb=1.0, epoch=1, trained_tokens=1
    )
    run.step(2, loss=0.9)
    run.part("backward")
    run.validate(1.0)
    run.validate(0.9)
    ours = {line.split("{")[0].split(" ")[0] for line in (writing + m.render() + run.render()).splitlines()}
    ours |= set(sysstat.BATTERY)  # on laptops, on either platform
    ours.add("vitals:idle_power_watts")  # recorded by Prometheus itself (rules_config)
    return {
        queries.MAC: ours | set(queries.MACMON_METRICS) | set(sysstat.METRICS) | set(sysstat.MAC_ONLY),
        queries.LINUX: ours | set(gpustat.METRICS) | set(sysstat.METRICS) | set(sysstat.LINUX_ONLY),
    }
