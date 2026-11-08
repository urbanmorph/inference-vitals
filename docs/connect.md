# Getting a model's numbers on the page

[← README](../README.md)

The machine half of the dashboard works on its own, whatever is running. The model half fills in when
something reports to `127.0.0.1:8400/metrics`, the address Prometheus reads. There are two ways; for a
fine-tune, see [training.md](training.md). Only one thing can report on 8400 at a time.

## Any engine with an HTTP API: the proxy

```sh
uv run inference-vitals proxy --to http://127.0.0.1:11434   # Ollama
uv run inference-vitals proxy --to http://127.0.0.1:1234    # LM Studio
uv run inference-vitals proxy --to http://127.0.0.1:8080    # llama.cpp server, mlx-lm / mlx-vlm server
```

Then point your app at `http://127.0.0.1:8401` instead of the engine. Every request goes through
unchanged and every reply streams back unchanged; on the way, the proxy measures time to first token,
tokens per second (live, while the answer is written), token counts, answers cut off at the token limit,
and requests the engine refused. Each model gets its own line on the charts, named from the request's
`model` field (a model given as a file path is named by its folder only).

It understands the OpenAI-style `/v1/chat/completions` and `/v1/completions`, which almost every local
engine offers, and Ollama's own `/api/chat` and `/api/generate`. When the engine reports exact counts
and timings (OpenAI `usage`, llama.cpp `timings`, Ollama's counts and durations), those are used;
otherwise tokens are counted as they stream in, which is close but not exact.

What a proxy cannot see is the engine's own GPU memory, so the model memory chart stays empty and "GPU
mem left" shows the memory left on the whole machine instead; the machine's memory, pressure and swap
panels still show everything. "Progress" and
"Context used" also stay empty, since a proxy knows neither how many requests are coming nor the
engine's context limit.

## Your own Python code: the library

Any engine (MLX, llama-cpp-python, PyTorch, a loop over an API), no extra packages:

```python
from inference_vitals.metrics import Metrics, serve

live = Metrics(total=len(pages), kv_per_token=0, labels={"model_name": "qwen3-8b-4bit"}, context_limit=32768)
serve(live, 8400)

for page in pages:
    live.started(page)
    # ... while the answer streams in:
    live.progress(tokens=n_tokens_so_far, writing_seconds=seconds_since_first_token, prompt_tokens=2900)
    live.finished(
        {
            "ttft_s": 1.8,
            "seconds": 21.0,
            "prompt_tokens": 2900,
            "generation_tokens": 310,
            "prompt_tps": 300.0,
            "generation_tps": 15.4,
            "peak_memory_gb": 19.1,
            "gpu_memory_limit_gb": 40.2,
            "context_used_tokens": 3210,
            "finish_reason": "stop",
            "parse_error": None,
            "warm": True,
        }
    )
```

Every field is optional; panels for fields you do not send stay empty. Tokens count as `progress`
reports them, so speeds, energy per token and (given `prompt_tokens`) context used stay live through a
long answer, such as a model that reasons before it answers. The `model_name` label puts each
model on its own line, so two runs can be compared on the same chart. `kv_bytes_per_token(config)` works
out the KV cache estimate from a model's `config.json`, including models that mix full and linear
attention layers.

This route can report everything, including the model's GPU memory and the context limit, so every
panel fills in. The metric names follow vLLM's where one exists, so dashboards built for vLLM can read
them too; every name is in [metrics.md](metrics.md).

Both routes carry numbers only. Prompts, answers and item ids never reach the numbers page, because any
program on the machine can read `127.0.0.1`; the proxy does not store or log what passes through it.

## Run conditions

`inference_vitals.conditions.snapshot()` (macOS only, for now) returns what the Mac was doing at a
moment: thermal and performance warnings, the CPU speed limit, mains or battery, the power mode, the GPU
memory limit setting, the macOS version, and any process using more than 20% CPU (by name only). Store it
with your results: a slow run on a hot or busy Mac then explains itself. Spotlight indexing and Photos
analysis (`mediaanalysisd`) on a new Mac are common culprits.
