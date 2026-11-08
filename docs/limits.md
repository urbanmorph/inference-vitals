# Privacy, overhead and limits

[← README](../README.md)

## Privacy

- Every service listens on `127.0.0.1`; nothing is reachable from the network.
- The page loads nothing from the internet: its script, styles and icon come from this package. It can
  only ask Prometheus two read-only questions (a reading now, and readings over a time range).
- Only numbers are collected. Prompts, answers and item ids never reach the numbers page; the proxy does
  not store or log what passes through it. Configuration, history and logs stay in
  `~/.local/state/inference-vitals`.
- Fine-tunes publish numbers only (loss, steps, speed, memory): `train` and `watch-training` pass the
  trainer's output through or read it, and keep none of it. The run's name is its one label, so do not
  put anything private in it.
- The battery is read on the machine (macOS's battery report, Linux's `/sys`), like everything else.
- Your electricity rate, theme and time range are kept in your browser only.
- With `--ui grafana`: Grafana's usage reporting, update checks, news feed and Gravatar are turned off,
  and so are the ways it could send anything off the machine: publishing snapshots to
  snapshots.raintank.io, public ("share externally") links, the plugin catalog, and the plugins it
  otherwise downloads from grafana.com on every start. It is view-only: sign-in and alerting are off.

## Overhead

The services take a few hundred MB of memory between them (Prometheus most of it; more with Grafana)
and very little CPU. An open page refreshes its tiles every 5 seconds, its charts every 30 and its
energy figures every minute, and does nothing while its tab is hidden. That is small next to a model,
but for a run where speed matters, close the tab or switch away: Prometheus keeps recording, and you can
look at the whole run afterwards. To measure the difference on your machine, run the same work with the
tab open and closed and compare tokens per second.

## What it cannot show

- **GPU use per core.** macOS reports one figure for the whole GPU.
- **Neural Engine use.** Only its power is reported. MLX does not use the Neural Engine, so it should
  stay near zero during a run; if it does not, something else is using it.
- **Image tokens apart from text tokens**, unless your code reports them.
- **The parts of a training step** (read, backward, write) when following a trainer's output: trainers
  print whole steps. A training loop that reports them itself can show them.
- **Intel Macs.** macmon needs Apple Silicon.
- **On Linux:** whole-machine power (only the GPUs' own power is reported; the CPU's needs root), AMD
  and Intel GPUs, and Windows.
