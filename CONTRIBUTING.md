# Contributing

Issues and pull requests are welcome, and reports from Linux with real NVIDIA GPUs especially.

## For a pull request

- Write the test first.
- Keep the dashboard described in code: the page in `src/inference_vitals/ui.py` (and `page/`), the
  Grafana dashboard in `monitor.py`. The page's tabs are taken from the Grafana dashboard's rows (each
  led by the page's own chart where it has one), so the two show the same charts. Do not edit the
  dashboard by hand in Grafana.
- A new metric goes in [docs/metrics.md](docs/metrics.md) too; a test fails until it does.
- Run the checks below before sending it.

## Checks

```sh
uv run pytest        # tests, including ones that check every chart on the page and in Grafana uses a
                     # metric that exists, and that the docs' links and metric list match the code; the
                     # page's JavaScript is tested too when Node is installed
uv run ruff check .
uv run ruff format --check .
sh docs/take-screenshots.sh                 # re-take the screenshots from a running dashboard (needs Chrome)
WHAT=training sh docs/take-screenshots.sh   # the training screenshot, during or just after a fine-tune
```

## Adding a trainer to `train` and `watch-training`

Capture a few lines of its real output (a step, a validation pass, a save; numbers only), add them to
`tests/test_follow.py` as a test, and teach `follow.Reader` in `src/inference_vitals/follow.py` to read
them. Then add a row to the trainers table in [docs/training.md](docs/training.md), saying what it was
tested against. The test keeps the reader honest if the trainer changes its format.

## Where things are

| Path | What |
| --- | --- |
| `src/inference_vitals/monitor.py` | `start`, `stop`, `status`; Prometheus and Grafana configuration; the Grafana dashboard |
| `src/inference_vitals/ui.py`, `page/` | The dashboard page and its server |
| `src/inference_vitals/metrics.py` | The library for model runs (port 8400) |
| `src/inference_vitals/training.py`, `follow.py` | Fine-tunes: the library, and reading a trainer's output |
| `src/inference_vitals/proxy.py` | The proxy in front of an engine |
| `src/inference_vitals/sysstat.py`, `gpustat.py` | The system exporter (memory, swap, battery) and the NVIDIA exporter |
| `src/inference_vitals/conditions.py` | Run conditions on a Mac |
