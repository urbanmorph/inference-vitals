# Install, run, troubleshoot, uninstall

[← README](../README.md)

## On a Mac

You need an Apple Silicon Mac (M1 or later) with [Homebrew](https://brew.sh). [uv](https://docs.astral.sh/uv/)
brings the Python it needs (3.11 or later); nothing is installed into your system Python.

```sh
brew install uv prometheus macmon
git clone https://github.com/urbanmorph/inference-vitals.git
cd inference-vitals
uv run inference-vitals start
open http://127.0.0.1:3000
```

`start` writes the configuration, starts the four services below (each only if it is not already
running) and waits until each one answers. Nothing starts by itself at login; run `start` when you want
to watch something.

| Service | Address | What it does |
| --- | --- | --- |
| macmon (Mac) or the NVIDIA exporter (Linux) | `127.0.0.1:9091` | Chip numbers: GPU (and on a Mac, CPU) use, clock speeds, power, temperatures, fans. No `sudo`. |
| System exporter | `127.0.0.1:9092` | Swap activity, free memory, swap in use and the battery (and on Linux, CPU use and temperature), from `vm_stat`, `sysctl` and `ioreg`, or `/proc` and `/sys`. |
| Prometheus | `127.0.0.1:9090` | Reads everything every 5 seconds and keeps 30 days of history. |
| Dashboard page | `127.0.0.1:3000` | The page, served by inference-vitals itself. It reads Prometheus through two read-only queries and nothing else; it cannot change anything. |

Model runs and fine-tunes report on `127.0.0.1:8400`, which Prometheus also reads (see
[connect.md](connect.md) and [training.md](training.md)).

**Prefer Grafana?** `brew install grafana`, then `uv run inference-vitals stop` and
`uv run inference-vitals start --ui grafana`. The same dashboard opens at
`http://127.0.0.1:3000/d/inference-vitals`, view-only, with sign-in, sharing, plugins and alerting off.
Grafana has no training mode: a fine-tune shows in its own folded **Training** row, while the band and
tiles at the top stay those of a model run.

## Commands

| Command | What it does |
| --- | --- |
| `uv run inference-vitals start` | Start the exporters, Prometheus and the page (each only if not running) |
| `uv run inference-vitals start --ui grafana` | The same, with Grafana in place of the page |
| `uv run inference-vitals status` | What is running, and whether each one answers |
| `uv run inference-vitals stop` | Stop everything `start` started |
| `uv run inference-vitals proxy --to URL` | Measure an engine's requests on their way through (port 8401); see [connect.md](connect.md) |
| `uv run inference-vitals train -- COMMAND` | Run a fine-tune and follow it from what it prints; see [training.md](training.md) |
| `uv run inference-vitals watch-training LOG` | Follow a fine-tune from its log file |

## On Linux with NVIDIA GPUs (experimental)

Tested against recorded `nvidia-smi`, `/proc` and `/sys` output, and run end to end in a Debian container
(Debian's own Prometheus 2.42) with a stand-in `nvidia-smi` playing two GPUs, a made-up laptop battery and
a fine-tune followed with `train`: every service started, all 105 of the page's queries ran without an
error, and the page drew in a browser in training mode with the battery chip. It has not run on real
NVIDIA hardware yet: if you run it, please open an issue with what you see. You need the NVIDIA
driver (which brings `nvidia-smi`), [uv](https://docs.astral.sh/uv/) and Prometheus (Grafana only for
`--ui grafana`):

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
system exporter reads `/proc` (memory, swap, CPU busy share), the CPU's own temperature sensor and, on a
laptop, `/sys/class/power_supply`. Without `nvidia-smi` the GPU numbers stay empty and the rest still
works. The proxy works the same on Linux. Windows is not supported (under WSL2, the Linux route may work).

## Troubleshooting

- **`prometheus: not found (brew install prometheus)`** (or macmon, or grafana): install what it names.
- **`<name>: NOT READY, see ~/.local/state/inference-vitals/logs/<name>.log`**: the log says why. The
  usual cause is a port already taken, often by a copy started with `brew services`; stop that one
  (`brew services stop prometheus`) and run `start` again.
- **`grafana is running on port 3000: run inference-vitals stop first`** (or `page is running…`): the
  other dashboard holds the port. `stop`, then `start` with the one you want.
- **The run's tiles stay empty and the band says "Nothing reporting"**: nothing is reporting to
  `127.0.0.1:8400`. Use the proxy or the library ([connect.md](connect.md)), and make sure only one of
  them runs.
- **A fine-tune shows no numbers with `train` or `watch-training`**: its trainer prints in a format the
  reader does not know (see [training.md](training.md)). Report from the training loop instead, or add the
  format ([CONTRIBUTING.md](../CONTRIBUTING.md)).
- **Some machine tiles stay empty on Linux**: GPU numbers need `nvidia-smi`; CPU temperature needs a
  `coretemp` or `k10temp` sensor, which virtual machines usually lack.
- **No battery chip on a laptop**: the system exporter reads no battery. On a Mac, check
  `ioreg -rn AppleSmartBattery` lists one; on Linux, `/sys/class/power_supply/BAT0`. Run `stop` and
  `start` after updating inference-vitals, so the exporter runs the new code.

## Uninstall

```sh
uv run inference-vitals stop
rm -rf ~/.local/state/inference-vitals
brew uninstall prometheus macmon   # on a Mac, if nothing else uses them (and grafana, if you added it)
```
