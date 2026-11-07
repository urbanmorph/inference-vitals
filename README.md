<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo-dark.svg">
    <img alt="inference-vitals" src="docs/brand/logo-light.svg" height="48">
  </picture>
</h1>

**One screen for running AI models on an Apple Silicon Mac, or on Linux with NVIDIA GPUs (experimental):
how fast the model is, how close it is to running out of memory, and how hot the machine is getting, on
the same timeline.**

![At a glance, during a run: speed, context, answers, memory, swap, GPU, heat and power](docs/screenshots/at-a-glance-dark.png)

Activity Monitor (or `nvtop`) makes you switch views and knows nothing about models. Benchmark apps time
the requests they send themselves. inference-vitals sits beside whatever you are running and records both
sides, the model and the machine, so you can see *why* a run slowed down: the GPU got hot, the system
started swapping, or a page was simply longer.

It works with **any model and any local engine**: Ollama, LM Studio, llama.cpp, the MLX servers, Jan,
LocalAI, or your own Python code. A small proxy in front of the engine measures each request as it
streams through; your code can also report directly.

It is [Prometheus](https://prometheus.io) with a dashboard page of its own (or, if you prefer,
[Grafana](https://grafana.com)), [macmon](https://github.com/vladkens/macmon) for the chip on a Mac (on
Linux, a small exporter that reads `nvidia-smi`), a small exporter for memory, the proxy, and a tiny Python
library, with no dependencies beyond Python itself. Everything runs on your machine and listens on
`127.0.0.1` only; the page loads nothing from the internet.

## What it shows

**At a glance**: a **Running** band naming each model that is reporting, what it is doing (Idle,
Reading (prefill) or Writing (decode)) and how far through its run it is; the **energy** the machine used
in the chosen time range and how much of it was the model's; then sixteen numbers, each drawn the way that
suits it (a progress bar, a speedometer, an arc with warning zones, a fill bar, a ring, steps for a state,
or a trend line where the pattern over time matters more than the reading now), and one chart:

| The run | The machine (Mac; differences on Linux below) |
| --- | --- |
| **Progress** through the current run | **GPU mem left**: memory left before macOS's limit for the GPU |
| **Writing now**: tokens per second, live, while the answer is written | **Memory free**, macOS's own memory-pressure level |
| **TTFT (median)**: time to first token | **Swap in/out**: bytes moving to and from swap right now |
| **Reading speed**: prompt tokens per second | **GPU busy** |
| **Context used** against the limit, live while the answer is written (prompt plus tokens so far) | **GPU temp** (amber at 85 °C, red at 95 °C) |
| **Answers cut off** at the token limit | **Thermal**: macOS's thermal state, live: Nominal, Fair (warm), Serious (slowing down), Critical |
| **Invalid answers** (not the format you asked for) | **Mac power**, the whole machine, in watts, against the highest in range |
| **Cold start TTFT**: the first answer after loading | **Energy/token**: the model's energy ÷ tokens written, over the chosen range |

On Linux with NVIDIA, the machine column changes where the hardware differs: **GPU mem left** is free
VRAM (summed over GPUs), **GPU slowdown** replaces Thermal (the GPU's own reasons for running below full
speed: None, Power cap, which is normal under full load, Thermal, Hardware), **GPU power** replaces Mac
power, and **Energy/token** counts the GPUs only. The folded sections show VRAM used against total per
GPU, GPU power against its limit, GPU and CPU temperatures, clocks, slowdown and fan speed.

The chart plots writing speed against GPU temperature, which is usually the first thing to look at
when a long run slows down. Every chart has a crosshair: point at any moment for each line's reading.

**Energy and cost.** The Running band adds up the power drawn over the chosen range (15 min to 7 days),
in Wh or kWh, and splits it: the **AI** share is the power above the machine's usual idle draw (the low
end of the last day's readings) while a model is reading or writing; the **rest** is everything else.
Energy/token is that AI energy divided by the tokens written in the same range, so the figures add up.
Add your electricity rate (per kWh, or per unit, as on your bill) and it shows the approximate cost of
both; the rate stays in your browser. Tariffs vary by country, state, tier and time of day, so the page
never guesses one. On Linux only the GPUs are metered.

Below that, four sections stay folded until you open them:

- **Speed detail**: TTFT per answer, median and 95th percentile, prefill and decode speed, model load time.
- **Context and limits**: context used per answer against the limit; prompt, cached and answer tokens.
- **Memory**: model memory (in use, peak, kept for reuse, KV cache estimate) against the GPU limit;
  swap activity; memory pressure.
- **Hardware**: GPU, performance and efficiency core use; GPU, CPU, Neural Engine and whole-Mac power;
  temperatures; clock speeds; thermal state; fans.

| Memory, opened | Hardware, opened |
| --- | --- |
| ![Model memory against the GPU limit, swap and memory pressure](docs/screenshots/memory.png) | ![Core use, power by part, temperatures, clocks and fans](docs/screenshots/power.png) |

Light and dark follow your system; the ◐ button switches (or add `?theme=light` to the address). The logo
links back here.

![The same view in the light theme](docs/screenshots/at-a-glance-light.png)

## Install

You need an Apple Silicon Mac (M1 or later) with [Homebrew](https://brew.sh).

```sh
brew install uv prometheus macmon
git clone https://github.com/urbanmorph/inference-vitals.git
cd inference-vitals
uv run inference-vitals start
open http://127.0.0.1:3000
```

`start` writes the configuration, starts the four services below (each only if it is not already
running) and waits until each one answers. `uv run inference-vitals status` shows what is running;
`uv run inference-vitals stop` stops all four.

| Service | Address | What it does |
| --- | --- | --- |
| macmon (Mac) or the NVIDIA exporter (Linux) | `127.0.0.1:9091` | Chip numbers: GPU (and on a Mac, CPU) use, clock speeds, power, temperatures, fans. No `sudo`. |
| Memory exporter | `127.0.0.1:9092` | Swap activity, free memory and swap in use (and on Linux, CPU use and temperature), from `vm_stat` and `sysctl` or `/proc`. |
| Prometheus | `127.0.0.1:9090` | Reads everything every 5 seconds and keeps 30 days of history. |
| Dashboard page | `127.0.0.1:3000` | The page, served by inference-vitals itself. It reads Prometheus through two read-only queries and nothing else; it cannot change anything. |

Nothing starts by itself at login; run `start` when you want to watch something.

**Prefer Grafana?** `brew install grafana`, then `uv run inference-vitals stop` and
`uv run inference-vitals start --ui grafana`. The same dashboard opens at
`http://127.0.0.1:3000/d/inference-vitals`, view-only, with sign-in, sharing, plugins and alerting off.

### Linux with NVIDIA GPUs (experimental)

Tested against recorded `nvidia-smi` and `/proc` output, and started end to end in a Debian container
with a stand-in `nvidia-smi` (every service answers and every dashboard query runs), but not yet on real
NVIDIA hardware: if you run it, please open an issue with what you see. You need the NVIDIA driver (which brings
`nvidia-smi`), [uv](https://docs.astral.sh/uv/) and Prometheus (Grafana only for `--ui grafana`):

```sh
# Prometheus: your distribution's package (Debian/Ubuntu: sudo apt install prometheus) or the release
# tarball from prometheus.io; only the `prometheus` binary needs to be on your PATH.
# If the package starts its own service, stop it first (sudo systemctl disable --now prometheus):
# inference-vitals runs its own copy on 127.0.0.1 with its own settings.
git clone https://github.com/urbanmorph/inference-vitals.git
cd inference-vitals
uv run inference-vitals start
xdg-open http://127.0.0.1:3000
```

On Linux the chip exporter on `127.0.0.1:9091` reads `nvidia-smi` instead of running macmon, and the
memory exporter reads `/proc` (memory, swap, CPU busy share) and the CPU's own temperature sensor. Without
`nvidia-smi` the GPU numbers stay empty and the rest still works. The proxy works the same on Linux; it
never cared which machine it runs on. Windows is not supported (under WSL2, the Linux route may work).

## Getting the model's numbers on it

The machine half of the dashboard works on its own, whatever is running. The model half fills in when
something reports to `127.0.0.1:8400/metrics`, the address Prometheus reads. There are two ways.

### Any engine with an HTTP API: the proxy

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

What a proxy cannot see is the engine's own GPU memory, so "GPU mem left" and the model memory chart
stay empty; the machine's memory, pressure and swap panels still show everything. "Progress" and
"Context used" also stay empty, since a proxy knows neither how many requests are coming nor the
engine's context limit.

### Your own Python code: the library

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
panel fills in. The proxy and the library both publish on port 8400, so run one at a time. The metric
names follow vLLM's where one exists, so dashboards built for vLLM can read them too.

Both routes carry numbers only. Prompts, answers and item ids never reach the numbers page, because any
program on the machine can read `127.0.0.1`; the proxy does not store or log what passes through it.

## Run conditions

`inference_vitals.conditions.snapshot()` (macOS only, for now) returns what the Mac was doing at a moment: thermal and
performance warnings, the CPU speed limit, mains or battery, the power mode, the GPU memory limit setting,
the macOS version, and any process using more than 20% CPU (by name only). Store it with your results:
a slow run on a hot or busy Mac then explains itself. Spotlight indexing and Photos analysis
(`mediaanalysisd`) on a new Mac are common culprits.

## Overhead

The four services take a few hundred MB of memory between them and very little CPU. An open dashboard
tab also redraws its charts every 10 seconds. That is small next to a model, but for a run where speed
matters, close the tab: Prometheus keeps recording, and you can look at the whole run afterwards. To
measure the difference on your Mac, run the same work with the tab open and closed and compare tokens
per second.

## Privacy

- Every service listens on `127.0.0.1`; nothing is reachable from the network.
- The page loads nothing from the internet: its script, styles and icon come from this package. It can
  only ask Prometheus two read-only questions (a reading now, and readings over a time range).
- Your electricity rate, theme and time range are kept in your browser only.
- With `--ui grafana`: Grafana's usage reporting, update checks, news feed and Gravatar are turned off,
  and so are the ways it could send anything off the machine: publishing snapshots to
  snapshots.raintank.io, public ("share externally") links, the plugin catalog, and the plugins it
  otherwise downloads from grafana.com on every start. It is view-only: sign-in and alerting are off.
- Only numbers are collected. Configuration, history and logs stay in `~/.local/state/inference-vitals`.

## What it cannot show

- **GPU use per core.** macOS reports one figure for the whole GPU.
- **Neural Engine use.** Only its power is reported. MLX does not use the Neural Engine, so it should
  stay near zero during a run; if it does not, something else is using it.
- **Image tokens apart from text tokens**, unless your code reports them.
- **Intel Macs.** macmon needs Apple Silicon.
- **On Linux:** whole-machine power (only the GPUs' own power is reported; the CPU's needs root), AMD
  and Intel GPUs, and Windows.

## Uninstall

```sh
uv run inference-vitals stop
rm -rf ~/.local/state/inference-vitals
brew uninstall prometheus macmon   # on a Mac, if nothing else uses them (and grafana, if you added it)
```

## Development

```sh
uv run pytest        # tests (the page's JavaScript too, with Node), including one that checks every
                     # chart on the page and in Grafana uses a metric that exists
uv run ruff check .
sh docs/take-screenshots.sh   # re-take the screenshots from a running dashboard (needs Google Chrome)
```

The page is `src/inference_vitals/page/`, described by `ui.py`; the Grafana dashboard is generated by
`monitor.py`, and the page's folded sections are taken from it, so the two show the same charts. Change
them there and run `start` again.

## Credits

inference-vitals runs [macmon](https://github.com/vladkens/macmon) (MIT) and
[Prometheus](https://github.com/prometheus/prometheus) (Apache-2.0), and optionally
[Grafana](https://github.com/grafana/grafana) (AGPL-3.0), installed separately (Homebrew on a Mac, your
package manager on Linux); none of them is bundled here.

## Licence

MIT. See [LICENSE](LICENSE).
