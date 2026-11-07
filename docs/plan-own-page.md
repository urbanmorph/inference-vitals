# Plan: inference-vitals' own page (Grafana optional)

**Status:** built (2026-11-07): the page is the default; Grafana with `--ui grafana`. Next: a week side by side, then screenshots from a run. **Decision:** inference-vitals stays a one-person, one-machine tool;
shared GPU servers are better served by vLLM's metrics, NVIDIA's DCGM exporter and a gateway such as LiteLLM,
shown in Grafana. For one person, Grafana is more than needed, so the default becomes a page of our own.

## Why

Grafana's strengths are switched off here (editing, exploration, alerting, plugins, sign-in). What is left
costs the most: the largest install, a few hundred MB of memory beside a model that needs every GB, a sign-in
button and favicon that cannot be changed, and layout limits (a trend line cannot sit inside a tile without
covering the number, so the tiles carry it in a strip below).

## What changes

- `inference-vitals start` serves the page itself on `127.0.0.1:3000`. Prometheus stays (recording, 30 days
  of history); Grafana is no longer installed by default.
- `inference-vitals start --ui grafana` keeps today's Grafana dashboard for anyone who wants it.
- One description of the dashboard (tiles, queries, units, thresholds, sections) in `monitor.py` drives both
  the page and the Grafana JSON, so they cannot drift apart.

## The page

- A Running band (each model, its phase, its progress and the energy used), then the run's tiles and the
  machine's tiles, each with the gauge that suits it; the writing-speed-against-heat chart;
  the four folded sections (speed, context, memory, hardware); Mac and Linux + NVIDIA variants.
- Light and dark from the system setting, with a switch; the project's own logo and favicon; no sign-in.
- Time range: 15 min, 1 h, 6 h, 24 h, 7 d; tiles refresh every 5 s, charts and range-wide figures every
  30 s, energy every minute, nothing while the tab is hidden.
- Each tile has the gauge that suits it (progress bar, speedometer, arc with zones, fill or split bar, ring,
  steps, ratio, or a trend line where the pattern matters more than the reading).
- Energy in the chosen range, split into the model's share (power above the usual idle draw while a model
  works) and the rest; Energy/token is that share ÷ tokens, so the figures add up. Cost only from a rate
  the user enters (kept in the browser); never guessed from the locale.
- A crosshair on every chart, with each line's reading at that moment.
- Plain HTML, CSS and JavaScript served from the package; charts drawn as SVG by our own small code (no
  library fetched from the internet; nothing leaves the machine).
- The page server only forwards read-only queries to Prometheus (`query` and `query_range`), on 127.0.0.1.

## Steps

1. Mockup with sample data, for the look. Done.
2. The dashboard description as data; Grafana JSON generated from it (tests: same queries as today).
3. Page server: static files plus the two forwarded Prometheus queries (tests: anything else refused).
4. The page: tiles and Running band, then charts, then folded sections; both themes; Mac and Linux.
5. `start`, `stop`, `status`, `--ui grafana`; README, screenshots and the install line updated.
6. A week beside Grafana; make the page the default when it covers everything.
