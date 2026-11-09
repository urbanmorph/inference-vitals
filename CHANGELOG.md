# Changes

Install or update with `uv tool install inference-vitals` / `uv tool upgrade inference-vitals`, then
`inference-vitals start`.

## 0.2.1

**Updating is now one step.** After `uv tool upgrade inference-vitals`, run `inference-vitals start`: it
sees that an older version is still running and restarts it. `inference-vitals --version` and the first
line of `status` show the version; so does the page's footer.

**`start` tells the truth about what started.**
- If another program already holds a port (often Prometheus or macmon run by `brew services`, or a
  second copy of inference-vitals), `start` names the port and the usual fix and starts nothing there,
  instead of saying "started" while showing someone else's numbers.
- A service that stops at once is reported with the last line of its log.
- `start` exits with an error when anything did not start, so scripts and AI agents can tell.
- Cutting its output short (`inference-vitals start | head`) no longer stops it halfway.
- On Linux without an NVIDIA GPU, `start` works (0.2.0 crashed there) and `status` says why the GPU
  exporter is not used.

**The page**
- Loads cleanly the first time: its server dropped most of a first visit's ~40 requests, leaving tiles
  empty until the next refresh.
- Charts with a limit end at the real limit: "Mac memory and swap in use" tops out at your Mac's own
  memory, not a round 100 GB. Other charts round up less.
- The GPU memory chart shows a fine-tune's peak against your Mac's GPU limit (read from Metal, as MLX
  reads it), even with no model run reporting one. If a fine-tune swaps, you can see why.
- The battery chip follows plugging in and out within seconds (it could lag a minute and a half).

**Fine-tuning**
- A fine-tune that was killed no longer holds the page for a week: an hour after it stops reporting, the
  page goes back to the model run. A finished one stays for a day, so last night's run is still there in
  the morning.
- A slow trainer shows up straight away (mlx-vlm on an 8B showed nothing for its first 10 steps), and
  its time per step and time left are the trainer's own (they read 9.6 s and 28 minutes for an 85 s
  step and a 4-hour run).
- During a fine-tune, Favourites shows its loss, its speed, its memory against the GPU limit, and swap.
  For a model run it now starts with four charts (the machine's memory joins), so its grid is full.

**Under the hood:** idle power is worked out once a minute by Prometheus (the costliest query, before);
fewer requests per refresh; the code is split into `queries.py`, `grafana.py` and `monitor.py`. Tested
on macOS and in a Debian container, and with Grafana 13.

## 0.2.0

First release on PyPI: `uv tool install inference-vitals`. The dashboard page and the Grafana dashboard
for model runs and fine-tunes on an Apple Silicon Mac, or on Linux with NVIDIA GPUs (experimental).
