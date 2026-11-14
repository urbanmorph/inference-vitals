<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo-dark.svg">
    <img alt="inference-vitals" src="docs/brand/logo-light.svg" height="48">
  </picture>
</h1>

[![Website](https://img.shields.io/badge/site-vitals.urbanmorph.com-73BF69?cacheSeconds=86400)](https://vitals.urbanmorph.com)
[![PyPI](https://img.shields.io/pypi/v/inference-vitals?cacheSeconds=3600)](https://pypi.org/project/inference-vitals/)
[![Python](https://img.shields.io/pypi/pyversions/inference-vitals?cacheSeconds=86400)](https://pypi.org/project/inference-vitals/)
[![Licence: MIT](https://img.shields.io/badge/license-MIT-blue?cacheSeconds=86400)](LICENSE)
[![Tests](https://github.com/urbanmorph/inference-vitals/actions/workflows/test.yml/badge.svg)](https://github.com/urbanmorph/inference-vitals/actions/workflows/test.yml)
[![Apple Silicon · Linux with NVIDIA](https://img.shields.io/badge/runs_on-Apple_Silicon_%C2%B7_Linux_%2B_NVIDIA-555?cacheSeconds=86400)](docs/install.md)

**One screen for running and fine-tuning AI models on an Apple Silicon Mac, or on Linux with NVIDIA GPUs
(experimental): how fast the model is (or how well it is learning), how close it is to running out of
memory, and how hot the machine is getting, on the same timeline.**

![At a glance, during a run: speed, context, answers, memory, swap, GPU, heat and power](docs/screenshots/at-a-glance-dark.png)

Activity Monitor (or `nvtop`) makes you switch views and knows nothing about models. Benchmark apps time
the requests they send themselves. inference-vitals sits beside whatever you are running and records both
sides, the model and the machine, so you can see *why* a run slowed down: the GPU got hot, the system
started swapping, or a page was simply longer.

## What you get

- **Model runs**, with any local engine (Ollama, LM Studio, llama.cpp, the MLX servers, Jan, LocalAI) or
  your own code: speed live while the answer is written, time to first token, context used, answers cut
  off or invalid.
- **Fine-tunes**: training and validation loss, steps and time left, and whether the model is still
  learning or has started memorising its examples.
- **The machine**: GPU memory left, memory pressure, swap, GPU use, temperatures, thermal state, power.
- **Energy**: what the machine used over any range, how much of it was the AI, and the cost at your rate.
- **Battery** on a laptop: the charge, and on battery how long it will last at this load, with a warning
  when it will not outlast the run.

It fits one screen: the live numbers stay in view while tabs swap the charts below them, and you star the
charts you want together in **Favourites**. Every tile and tab: [docs/dashboard.md](docs/dashboard.md).

## Quick start

On an Apple Silicon Mac with [Homebrew](https://brew.sh):

```sh
brew install uv prometheus macmon
uv tool install inference-vitals
inference-vitals start
open http://127.0.0.1:3000
```

The machine's numbers appear straight away. Linux with NVIDIA, Grafana, troubleshooting:
[docs/install.md](docs/install.md).

To update, then pick up the new version (`start` restarts whatever an older one left running):

```sh
uv tool upgrade inference-vitals
inference-vitals start
```

What changed in each release: [CHANGELOG.md](CHANGELOG.md).

## Connect what you run

| You run… | Then | More |
| --- | --- | --- |
| An engine with an HTTP API (Ollama, LM Studio, llama.cpp, MLX servers) | `inference-vitals proxy --to http://127.0.0.1:11434` and point your app at `http://127.0.0.1:8401` | [connect.md](docs/connect.md) |
| Your own Python code | Three calls from `inference_vitals.metrics` | [connect.md](docs/connect.md#your-own-python-code-the-library) |
| A fine-tune (mlx-vlm, mlx-lm, or any loop) | `inference-vitals train -- <your training command>` | [training.md](docs/training.md) |

## Privacy

Everything runs on your machine and listens on `127.0.0.1` only; the page loads nothing from the internet;
only numbers are collected, never prompts, answers or file names. More: [docs/limits.md](docs/limits.md).

## Documentation

| | |
| --- | --- |
| [Install](docs/install.md) | Mac and Linux, commands, Grafana, troubleshooting, uninstall |
| [Connect](docs/connect.md) | The proxy and the Python library for model runs; run conditions |
| [Training](docs/training.md) | Following a fine-tune three ways; how "Learning?" is decided |
| [Dashboard](docs/dashboard.md) | Every tile, training mode, energy and cost, battery, address options |
| [Metrics](docs/metrics.md) | Every metric name, for other tools that read Prometheus |
| [Limits](docs/limits.md) | Privacy in full, overhead, what it cannot show |
| [AGENTS.md](AGENTS.md) | For coding agents helping you: tasks, checks, symptoms, rules |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Tests, checks, adding a trainer, releasing, where things are |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each release |

It is [Prometheus](https://prometheus.io) with a dashboard page of its own (or
[Grafana](https://grafana.com)), [macmon](https://github.com/vladkens/macmon) for the chip on a Mac (on
Linux, a small exporter that reads `nvidia-smi`), small exporters for memory and the battery, the proxy,
and a tiny Python library, with no dependencies beyond Python itself.

## Credits

inference-vitals runs [macmon](https://github.com/vladkens/macmon) (MIT) and
[Prometheus](https://github.com/prometheus/prometheus) (Apache-2.0), and optionally
[Grafana](https://github.com/grafana/grafana) (AGPL-3.0), installed separately (Homebrew on a Mac, your
package manager on Linux); none of them is bundled here.

## Licence

MIT. See [LICENSE](LICENSE).
