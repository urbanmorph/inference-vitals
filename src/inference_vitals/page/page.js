// inference-vitals' page. Everything it shows is described by /api/spec; numbers come from Prometheus
// through the page server's two read-only routes. Nothing is fetched from anywhere else.
"use strict";

const TILES_EVERY = 5000, CHARTS_EVERY = 30000, ENERGY_EVERY = 60000;
const $ = (id) => document.getElementById(id);
const store = {  // per-browser conveniences; the page works without them
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private window */ } },
};
// ?theme=, ?range= and ?view= (the tab shown), for links and screenshots
const asked = typeof location === "undefined" ? new URLSearchParams() : new URLSearchParams(location.search);
let SPEC = null, range = store.get("iv-range") || "1h";
const chosen = () => SPEC.ranges.find((r) => r.key === range);  // the chosen range: key, label, seconds, step

// ---- Prometheus ----
async function prom(path, params) {
  const r = await fetch(`${path}?${new URLSearchParams(params)}`);
  const body = await r.json();
  if (body.status !== "success") throw new Error(body.error || r.statusText);
  return body.data.result;
}
const instant = (query) => prom("/api/v1/query", { query }).catch(() => []);
// within one refresh, a query asked by two tiles (or a tile and the band) goes out once
const sharedAsk = (cache, key, ask) => { if (!cache.has(key)) cache.set(key, ask()); return cache.get(key); };
let asking = new Map();
const one = (query) => sharedAsk(asking, query, () => oneNow(query));
async function oneNow(query) {
  const r = await instant(query);
  const v = r.length ? parseFloat(r[0].value[1]) : NaN;
  return Number.isFinite(v) ? v : null;
}
const stepFor = (points, span = chosen().seconds) => Math.max(span < 600 ? 1 : 5, Math.round(span / points));
function rangeQuery(query, points = 240, win = null) {  // win: [start, end] instead of the chosen range
  const [start, end] = win || rangeWindow();
  return prom("/api/v1/query_range", { query, start, end, step: stepFor(points, end - start) }).catch(() => []);
}
const STALE = 90;  // seconds unread before a run that says it is training counts as stopped (killed)
function runState(phase, alive, now) {  // "training", "stale", "finished" or "none"
  if (phase == null) return "none";
  if (phase <= 0) return "finished";
  return alive !== null && alive !== undefined && now - alive <= STALE ? "training" : "stale";
}
function runWindow(start, end, running, now) {  // a fine-tune's charts: its whole run, 5% room either side
  if (start == null) return null;
  const last = running || end == null ? now : end, pad = (last - start) * 0.05 || 30;
  return [start - pad, last + pad];
}
const rangeWindow = () => [Date.now() / 1000 - chosen().seconds, Date.now() / 1000];  // the chosen range, ending now
// a query's placeholders: {range} always, {step} and {base} for the energy sums
const fill = (q, step = "", base = "") => q.replaceAll("{range}", range).replaceAll("{step}", step).replaceAll("{base}", base);
// Answers about the whole range (a top speed, a trend) change slowly: asked at most every CHARTS_EVERY
const memo = new Map();
function slow(key, ask) {
  const hit = memo.get(key), now = Date.now();
  if (hit && now - hit.at < CHARTS_EVERY) return hit.value;
  const value = ask();
  memo.set(key, { at: now, value });
  return value;
}

// ---- numbers ----
function compact(v, d, from = 1e4) {  // 12.3k, 4.5M: large numbers stay short for a tile or an axis
  const a = Math.abs(v), short = (x) => String(+x.toFixed(1));
  if (a >= 1e6) return `${short(v / 1e6)}M`;
  if (a >= from) return `${short(v / 1e3)}k`;
  return v.toFixed(d(v));
}
function axisText(v, unit) {  // axis labels: always compact, so they fit the margin
  const f = fmt(v, unit), plain = !["bytes", "decbytes", "Bps"].includes(unit);
  return plain && Math.abs(v) >= 1000 ? `${compact(v, () => 0, 1000)}${f.u ? ` ${f.u}` : ""}` : f.u ? `${f.n} ${f.u}` : f.n;
}
function fmt(v, unit, digits) {
  if (v == null || !Number.isFinite(v)) return { n: "–", u: "" };
  const d = (x) => (digits !== undefined ? digits : Math.abs(x) >= 100 ? 0 : Math.abs(x) >= 10 ? 1 : 2);
  const scaled = (x, steps) => { let i = 0; while (Math.abs(x) >= 1000 && i < steps.length - 1) { x /= 1000; i++; } return [x, steps[i]]; };
  switch (unit) {
    case "bytes": case "decbytes": { const [x, u] = scaled(v, ["B", "kB", "MB", "GB", "TB"]); return { n: x.toFixed(d(x)), u }; }
    case "Bps": { const [x, u] = scaled(v, ["B/s", "kB/s", "MB/s", "GB/s"]); return { n: x.toFixed(x < 10 && u !== "B/s" ? 1 : 0), u }; }
    case "percentunit": return { n: (v * 100).toFixed(d(v * 100)), u: "%" };
    case "percent": return { n: v.toFixed(d(v)), u: "%" };
    case "watt": return { n: v.toFixed(d(v)), u: "W" };
    case "celsius": return { n: v.toFixed(1), u: "°C" };
    case "none": case undefined: case "": return { n: v !== 0 && Math.abs(v) < 0.01 ? v.toExponential(1) : compact(v, d), u: "" };
    default: return { n: compact(v, d), u: unit.replace(/^suffix: */, "") };
  }
}
const text = (v, unit, digits) => { const f = fmt(v, unit, digits); return f.u ? `${f.n} ${f.u}` : f.n; };
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function tone(v, t) {
  if (v === null || t.warn === undefined) return "plain";
  if (t.low_is_bad) return v <= t.bad ? "bad" : v <= t.warn ? "warn" : "ok";
  return v >= t.bad ? "bad" : v >= t.warn ? "warn" : "ok";
}
const color = (t) => `var(--${t === "plain" ? "text" : t})`;
function energyText(wh) {
  if (wh === null) return "–";
  return wh >= 1000 ? `${(wh / 1000).toFixed(wh >= 10000 ? 1 : 2)} kWh` : `${wh.toFixed(wh >= 100 ? 0 : 1)} Wh`;
}

// ---- gauges (shared by every tile) ----
const xy = (cx, cy, r, a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
function arcPath(cx, cy, r, a0, a1) {
  const [x0, y0] = xy(cx, cy, r, a0), [x1, y1] = xy(cx, cy, r, a1);
  return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}
function arc(value, min, max, label, unit, t, zones = []) {
  const A0 = Math.PI * 5 / 6, SWEEP = Math.PI * 4 / 3, cx = 60, cy = 42, r = 34;
  const at = (v) => A0 + SWEEP * Math.min(1, Math.max(0, (v - min) / ((max - min) || 1)));
  let g = `<path d="${arcPath(cx, cy, r, A0, A0 + SWEEP)}" stroke="var(--chip)" stroke-width="7" fill="none" stroke-linecap="round"/>`;
  for (const [from, to, z] of zones) g += `<path d="${arcPath(cx, cy, r + 6, at(from), at(to))}" stroke="${color(z)}" stroke-width="2" fill="none" opacity=".55"/>`;
  if (value !== null) g += `<path d="${arcPath(cx, cy, r, A0, Math.max(A0 + .02, at(value)))}" stroke="${color(t)}" stroke-width="7" fill="none" stroke-linecap="round"/>`;
  g += `<text x="${cx}" y="${cy + 4}" text-anchor="middle" style="fill:${color(t)};font-size:16px;font-weight:600">${esc(label)}</text>`;
  g += `<text x="${cx}" y="${cy + 16}" text-anchor="middle" style="font-size:9px">${esc(unit)}</text>`;
  const end = (v) => compact(v, (x) => (Number.isInteger(x) ? 0 : 1));  // scale ends: 0, 25, 2.5, 12k
  g += `<text x="${xy(cx, cy, r, A0)[0] + 2}" y="${cy + 31}" text-anchor="middle">${esc(end(min))}</text>`;
  g += `<text x="${xy(cx, cy, r, A0 + SWEEP)[0] - 2}" y="${cy + 31}" text-anchor="middle">${esc(end(max))}</text>`;
  return `<svg class="arc" viewBox="0 0 120 76" aria-hidden="true">${g}</svg>`;
}
function ring(fraction, label, unit, t) {
  const cx = 60, cy = 38, r = 30, c = 2 * Math.PI * r, f = Math.min(1, Math.max(0, fraction || 0));
  return `<svg class="arc" viewBox="0 0 120 76" aria-hidden="true">
    <circle cx="${cx}" cy="${cy}" r="${r}" stroke="var(--chip)" stroke-width="7" fill="none"/>
    ${fraction === null ? "" : `<circle cx="${cx}" cy="${cy}" r="${r}" stroke="${color(t)}" stroke-width="7" fill="none" stroke-linecap="round" stroke-dasharray="${(c * f).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 ${cx} ${cy})"/>`}
    <text x="${cx}" y="${cy + 4}" text-anchor="middle" style="fill:${color(t)};font-size:15px;font-weight:600">${esc(label)}<tspan style="font-size:9px;font-weight:500">${esc(unit)}</tspan></text></svg>`;
}
function bar(pct, t, ticks = [], fillColor) {
  const w = Math.min(100, Math.max(0, pct || 0));
  return `<div class="barwrap"><div class="bar"><span style="width:${w}%;background:${fillColor || color(t)}"></span>${ticks.map((x) => `<i class="tick" style="left:${x}%"></i>`).join("")}</div></div>`;
}
function spark(points) {
  const values = points.map((p) => p[1]).filter(Number.isFinite);
  if (values.length < 2) return `<svg class="spark" viewBox="0 0 100 30" aria-hidden="true"></svg>`;
  const max = Math.max(...values), min = Math.min(...values), span = (max - min) || 1;
  const t0 = points[0][0], t1 = points[points.length - 1][0] || t0 + 1;
  const line = points.map((p, i) => `${i ? "L" : "M"}${(((p[0] - t0) / ((t1 - t0) || 1)) * 100).toFixed(2)} ${(27 - ((p[1] - min) / span) * 24).toFixed(2)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
    <path d="${line} L100 30 L0 30 Z" fill="var(--trend)" opacity=".12"/>
    <path d="${line}" fill="none" stroke="var(--trend)" stroke-width="1.4" vector-effect="non-scaling-stroke"/></svg>`;
}
const stepTone = (i) => (i === null ? "plain" : i === 0 ? "ok" : i === 1 ? "warn" : "bad");  // states, mildest first
function steps(names, i) {
  return `<div class="steps">${names.map((_, k) => `<span style="${i !== null && k <= i ? `background:${color(stepTone(k))};opacity:${k === i ? 1 : .45}` : ""}"></span>`).join("")}</div>
    <div class="steps-l">${names.map((x) => `<span>${esc(x)}</span>`).join("")}</div>`;
}
function niceMax(v) {  // the next round number at or above v: 1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6 or 8 × a power of ten
  if (!v || v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map((m) => m * p).find((x) => x >= v * 0.999);
}
function axisTop(values, limits) {  // a chart with a limit line ends at the limit, unless a reading is above it
  const top = Math.max(0, ...values), limit = limits.length ? Math.max(...limits) : 0;
  return limit && top <= limit ? limit : niceMax(Math.max(top, limit));
}

// ---- tiles ----
const head = (title, v, unit, t, digits, shown) => { const f = shown === undefined ? fmt(v, unit, digits) : { n: shown, u: "" };
  return `<div class="name">${esc(title)}</div><div class="value ${t}">${esc(f.n)}<span class="unit">${esc(f.u)}</span></div>`; };
const sub = (s) => `<div class="sub">${esc(s || "")}</div>`;
function ofText(used, limit, tail) {  // "43.9 of 51.5 GB in use": short enough for a tile's note
  const u = fmt(used, "bytes"), l = fmt(limit, "bytes");
  return `${u.n}${u.u === l.u ? "" : ` ${u.u}`} of ${l.n} ${l.u}${tail}`;
}

// One renderer per tile kind: (tile, its value, the value scaled for display, its tone) -> the tile's HTML.
const named = (t) => `<div class="name">${esc(t.title)}</div>`;
const TILES = {
  async progress(t) {  // done / total, both asked anyway
    const [done, total] = await Promise.all([one(t.done_query), one(t.total_query)]);
    const share = total && done !== null ? done / total : null;
    return head(t.title, share === null ? null : share * 100, "%", share === null ? "plain" : "ok", 0)
      + sub(total ? `${done} of ${total} ${t.of || "pages"}` : "no run reporting") + bar((share || 0) * 100, "ok");
  },
  async speed(t, v) {  // a speedometer against the highest in range
    const top = await slow(`${range} ${t.max_query}`, () => one(fill(t.max_query))), max = niceMax(Math.max(top || 0, v || 0));
    const f = fmt(v, t.unit);
    return named(t) + arc(v, 0, max, f.n, f.u || t.unit, v === null || t.unit === "W" ? "plain" : "ok")
      + `<div class="sub center">${top === null ? "" : `top in range ${text(top, t.unit)}`}</div>`;
  },
  async zones(t, v, scaled, tn) {  // an arc with its warning zones
    return named(t) + arc(v, t.min, t.max, fmt(v, t.unit).n, t.unit, tn, [[t.min, t.warn, "ok"], [t.warn, t.bad, "warn"], [t.bad, t.max, "bad"]]);
  },
  async bar(t, v, scaled, tn) {
    let note = "";
    if (t.used_query) {
      const [used, limit] = await Promise.all([one(t.used_query), one(t.limit_query)]);
      if (used !== null && limit) note = `${Math.round(used).toLocaleString()} of ${Math.round(limit).toLocaleString()} tokens`;
    }
    return head(t.title, scaled, t.unit, tn) + sub(note) + bar(scaled, tn, t.ticks);
  },
  async split(t, v, scaled, tn) {  // with model_query: no model reporting its own figures means these are the machine's
    const [used, limit, model] = await Promise.all([one(t.used_query), one(t.limit_query), t.model_query ? one(t.model_query) : 1]);
    return head(t.title, v, t.unit, tn) + sub(used !== null && limit ? ofText(used, limit, model === null ? ", machine" : " in use") : "")
      + bar(limit ? (used / limit) * 100 : 0, tn, [], "var(--muted)");
  },
  async ring(t, v, scaled) {
    return named(t) + ring(v, fmt(scaled, "").n, "%", v === null ? "plain" : "ok");
  },
  async steps(t, v) {  // a state: one of t.names
    const i = v === null ? null : Math.round(v);
    return head(t.title, null, "", stepTone(i), 0, i === null ? "–" : t.names[i] || "–") + steps(t.names, i);
  },
  async ratio(t, v, scaled, tn) {
    const of = await one(t.of_query);
    return head(t.title, v, "", tn, 0) + sub(of ? `${v ?? 0} of ${of} pages` : "") + bar(of ? Math.max(v ? 2 : 0, (v / of) * 100) : 0, tn);
  },
  async spark(t, v, scaled, tn) {  // the number, with its trend over the range
    const series = await slow(`${range} ${t.query}`, () => rangeQuery(t.query, 60));
    const points = series.length ? series[0].values.map(([x, y]) => [x, parseFloat(y)]) : [];
    const best = t.best_query ? await one(t.best_query) : null;
    return head(t.title, v, t.unit, tn) + (best === null ? "" : sub(`best ${text(best, t.unit)}`)) + spark(points);
  },
  async "energy-per-token"(t) {  // while training: per trained token
    const e = state.energy, training = state.mode === "training", tokens = e && (training ? e.trained : e.tokens);
    const jt = e ? perToken(e, state.mode) : null;
    return head(training ? "Energy/trained token" : t.title, jt, t.unit, "plain")
      + sub(jt === null ? "AI energy ÷ tokens" : `over ${text(tokens, "none")} ${training ? "trained " : ""}tokens`);
  },
  async plain(t, v) {  // a number and a note
    return head(t.title, v, t.unit, "plain") + sub(t.note);
  },
};
async function renderTile(t) {
  const v = t.query ? await one(fill(t.query)) : null;
  const scaled = v === null ? null : v * (t.scale || 1);
  return (TILES[t.kind] || TILES.plain)(t, v, scaled, tone(scaled, t));
}
async function renderTiles() {
  await Promise.all(["run", "machine"].map(async (group) => {
    const tiles = group === "run" && state.mode === "training" ? SPEC.training.tiles : SPEC.tiles[group];
    const html = await Promise.all(tiles.map(async (t) => {
      const body = await renderTile(t).catch(() => head(t.title, null, "", "plain"));
      const gauge = ["speed", "zones", "ring"].includes(t.kind) ? " gauge" : "";
      return `<div class="tile${gauge}" title="${esc(t.title)}">${body}</div>`;
    }));
    $(group).innerHTML = html.join("");
  }));
}

// ---- clock ----
function clockText(date, locale, timeZone) {  // "23:01:05 IST": local time, with the zone every time is in
  const parts = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    timeZone, timeZoneName: "short" }).formatToParts(date);
  const get = (type) => (parts.find((p) => p.type === type) || {}).value || "";
  return `${get("hour")}:${get("minute")}:${get("second")} ${get("timeZoneName")}`.trim();
}

// joules per token: the energy of that kind of work only (answering, or training) over its own tokens
function perToken(e, mode) {
  const [wh, tokens] = mode === "training" ? [e.aiTraining, e.trained] : [e.aiRun, e.tokens];
  return wh == null || !tokens ? null : +((wh * 3600) / tokens).toFixed(4);
}

// ---- which run the page is about, and the battery ----
const state = { energy: null, mode: "inference", left: null, window: null };  // window: the latest fine-tune's span  // left: seconds the current run still needs
// How long a fine-tune that has ended keeps the page, after its last report: one that finished, a day (last
// night's run is still there in the morning); one that stopped reporting (killed), an hour.
const HOLD = { finished: 24 * 3600, stale: 3600 };
function modeOf(trainingAt, runAt, how = "training", now = Date.now() / 1000) {
  // whichever reported last: a fine-tune, or a model run (the default); an ended fine-tune only for HOLD
  if (trainingAt == null || (runAt != null && runAt >= trainingAt)) return "inference";
  return how in HOLD && now - trainingAt > HOLD[how] ? "inference" : "training";
}
async function decideMode() {
  const m = SPEC.mode, band = SPEC.training.band;  // a week of readings: asked at the charts' pace
  const [t, r, phase, alive] = await slow("mode", () =>
    Promise.all([one(m.training_query), one(m.run_query), one(band.phase_query), one(band.alive_query)]));
  const now = Date.now() / 1000;
  state.mode = modeOf(t, r, runState(phase, alive, now), now);
  $("band-label").textContent = state.mode === "training" ? "Training" : "Running";
}
function batteryText(b, left) {  // a laptop's battery: on power its charge; on battery the time left at its draw now
  if (b.percent == null) return { hidden: true, text: "", warn: false };  // no battery
  if (b.onPower) return { hidden: false, text: `🔌 ${Math.round(b.percent)}% · on power`, warn: false };
  if (!b.draw || !b.wh) return { hidden: false, text: `🔋 ${Math.round(b.percent)}%`, warn: false };
  const secs = (b.wh / b.draw) * 3600, warn = left !== null && left !== undefined && secs < left;
  return { hidden: false, warn,
    text: `🔋 ${Math.round(b.percent)}% · ${secs < 30 ? "" : "~"}${duration(secs)} at this load${warn ? ` (the run needs ~${duration(left)})` : ""}` };
}
async function renderBattery() {
  const q = SPEC.battery;
  const percent = await one(q.percent_query);  // no battery (a desktop): one query, and nothing more
  const [wh, onPower, draw] = percent == null ? [] : await Promise.all([one(q.wh_query), one(q.on_power_query), one(q.draw_query)]);
  const b = batteryText({ percent, wh, onPower, draw }, state.left), el = $("battery");
  el.hidden = b.hidden;
  el.textContent = b.text;
  el.classList.toggle("warn", b.warn);
}

// ---- running band and energy ----
function duration(s) {
  if (s === null || !Number.isFinite(s)) return "";
  const m = Math.round(s / 60);
  if (m < 1) return "under a minute";
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}
async function trainingWindow() {  // the latest fine-tune's span, in either mode: its charts span the run
  const b = SPEC.training.band;
  const [started, updated, phase, alive] = await slow("window", () =>
    Promise.all([one(b.started_query), one(b.updated_query), one(b.now_phase_query), one(b.alive_query)]));
  const now = Date.now() / 1000;
  state.window = runWindow(started, updated, runState(phase, alive, now) === "training", now);
}
async function renderTrainingBand() {
  const b = SPEC.training.band, runs = await instant(b.phase_query);
  const [part, step, total, epoch, epochs, perStep, alive] = await Promise.all(
    [b.part_query, b.step_query, b.total_query, b.epoch_query, b.epochs_query, b.step_seconds_query, b.alive_query].map(one));
  const now = Date.now() / 1000;
  $("models").innerHTML = runs.map((p) => {
    const i = Math.round(parseFloat(p.value[1])), how = runState(i, alive, now);
    const pills = how === "stale" ? `<span class="on">stopped reporting</span>` : b.names.slice(1).map((n, k) => {
      const on = i === k + 1, label = on && k === 0 && part ? `${n} · ${b.parts[Math.round(part)] || ""}` : n;
      return `<span class="${on ? "on" : ""}">${on ? '<span class="dot"></span>' : ""}${esc(label)}</span>`;
    }).join("") + (i === 0 ? `<span class="on">finished</span>` : "");
    return `<span class="model">${esc(p.metric.run_name || "training")} <span class="phases">${pills}</span></span>`;
  }).join("") || `<span class="idle">No fine-tune has reported this week.</span>`;
  const running = runs.some((p) => runState(Math.round(parseFloat(p.value[1])), alive, now) === "training");
  state.left = running && total && step !== null && perStep ? (total - step) * perStep : null;
  $("run-meta").textContent = total ? `step ${step} of ${total}${epochs ? ` · epoch ${epoch ?? 1} of ${epochs}` : ""}`
    + `${state.left ? ` · ${leftText(state.left)}` : ""}` : "";
}
const leftText = (s) => (Math.round(s / 60) < 1 ? "under a minute left" : `about ${duration(s)} left`);
async function renderRunning() {
  if (state.mode === "training") return renderTrainingBand();
  const r = SPEC.running, phases = await instant(r.phase_query);
  $("models").innerHTML = phases.length
    ? phases.map((p) => { const i = Math.round(parseFloat(p.value[1]));
        return `<span class="model">${esc(p.metric.model_name || "model")} <span class="phase">${i > 0 ? '<span class="dot"></span>' : ""}${esc(r.names[i] || "")}</span></span>`; }).join("")
    : `<span class="idle">Nothing reporting. The machine numbers below still update.</span>`;
  const [done, total, perPage] = await Promise.all([one(r.done_query), one(r.total_query), one(r.seconds_per_page_query)]);
  const left = total && done !== null && perPage ? (total - done) * perPage : null;
  state.left = left;
  $("run-meta").textContent = total ? `page ${done} of ${total}${left ? ` · ${leftText(left)}` : ""}` : "";
}
async function renderEnergy() {
  const e = SPEC.energy, step = chosen().step;
  const filled = (q, base) => fill(q, step, base);
  const base = await one(e.idle_query);
  const ai_ = (q) => (base === null ? null : one(filled(q, base)));
  const [total, ai, aiRun, aiTraining, tokens, trained] = await Promise.all([
    one(filled(e.total_query)), ai_(e.ai_query), ai_(e.ai_run_query), ai_(e.ai_training_query),
    one(filled(e.tokens_query)), one(filled(e.trained_tokens_query)),
  ]);
  state.energy = { total, ai, aiRun, aiTraining, tokens, trained };
  $("energy").textContent = energyText(total);
  $("energy-label").textContent = `in the last ${chosen().label} · ${e.meter}`;
  const share = total && ai !== null ? Math.round((ai / total) * 100) : null;
  $("energy-ai").innerHTML = ai === null ? "" : `AI <b>${energyText(ai)}</b>${share === null ? "" : ` (${share}%)`} · rest ${energyText(Math.max(0, total - ai))}`;
  const rate = parseFloat(store.get("iv-rate")), cur = store.get("iv-currency") || "₹";
  const money = (wh) => `${cur}${((wh / 1000) * rate).toFixed(2)}`;
  $("cost").innerHTML = rate > 0 && total !== null
    ? `≈ ${money(total)}${ai !== null ? `, AI ${money(ai)}` : ""} at ${esc(cur)}${rate}/kWh · <a id="set-rate">change</a>`
    : `<a id="set-rate">add your electricity rate</a> to see the cost`;
  $("set-rate").onclick = () => $("rate-box").classList.toggle("open");
}

// ---- charts (the main one and every folded panel) ----
function nearest(points, t) {  // index of the point closest in time to t (points sorted by time)
  let lo = 0, hi = points.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (points[mid][0] < t) lo = mid; else hi = mid; }
  return Math.abs(points[hi][0] - t) < Math.abs(points[lo][0] - t) ? hi : lo;
}
function readout(series, t, maxGap) {  // each line's reading nearest t, if one was recorded close enough
  const rows = [];
  series.forEach((s, index) => {
    if (!s.points.length) return;
    const p = s.points[nearest(s.points, t)];
    if (Math.abs(p[0] - t) <= maxGap) rows.push({ name: s.name, value: text(p[1], s.unit), index });
  });
  return rows;
}
function hover(el, ev) {  // the crosshair: a line at the pointer and a tooltip with every line's reading
  const p = el.plot, svg = el.querySelector("svg"), tip = el.querySelector(".tip"), line = el.querySelector(".xhair");
  if (!p || !svg) return;
  const box = svg.getBoundingClientRect(), x = ((ev.clientX - box.left) * p.width) / box.width;
  if (x < p.left || x > p.width - p.right) { tip.hidden = true; line.style.display = "none"; return; }
  const t = p.start + ((x - p.left) / (p.width - p.left - p.right)) * (p.end - p.start);
  const rows = readout(p.series, t, p.step * 1.5);
  line.setAttribute("x1", x); line.setAttribute("x2", x); line.style.display = "";
  const when = new Date(t * 1000).toLocaleString([], { weekday: p.end - p.start > 86400 ? "short" : undefined, hour: "2-digit", minute: "2-digit", second: p.end - p.start <= 3600 ? "2-digit" : undefined });
  tip.innerHTML = `<div class="when">${esc(when)}</div>` + (rows.length
    ? rows.map((r) => `<div><i style="background:${p.colors[r.index]}"></i>${esc(r.name)} <b>${esc(r.value)}</b></div>`).join("")
    : `<div class="none">nothing recorded here</div>`);
  tip.hidden = false;
  const px = ev.clientX - el.getBoundingClientRect().left;
  tip.style.left = `${px + 14 + tip.offsetWidth > el.clientWidth ? px - 14 - tip.offsetWidth : px + 14}px`;
  tip.style.top = `${ev.clientY - el.getBoundingClientRect().top + 12}px`;
}
const label = (legend, metric) => (legend || "").replace(/\{\{\s*(\w+)\s*\}\}/g, (_, k) => metric[k] || "").trim() || metric.model_name || "";
const PALETTE = ["var(--accent)", "#e3b341", "#58a6ff", "#bc8cff", "#39c5cf", "var(--muted)"];  // heat is the right axis's own
const star = (el, panel) => { const on = el.dataset.fav === "1";
  return `<button class="star${on ? " on" : ""}" data-star="${esc(panel.title)}" title="${on ? "Remove from" : "Add to"} Favourites">${on ? "★" : "☆"}</button>`; };
async function renderChart(el, panel, win = null) {
  const series = [];
  await Promise.all(panel.targets.map(async (t, ti) => {
    for (const s of await rangeQuery(t.expr, 240, win)) {
      series.push({ order: ti, axis: t.axis || "left", unit: t.unit || panel.unit, name: label(t.legend, s.metric), limit: !!t.limit,
        points: s.values.map(([x, y]) => [x, parseFloat(y)]).filter((p) => Number.isFinite(p[1])) });
    }
  }));
  series.sort((a, b) => a.order - b.order);
  const svgW = el.clientWidth || 600, H = el.dataset.h ? +el.dataset.h : 180;
  if (!series.some((s) => s.points.length)) {
    el.innerHTML = `<h3>${esc(panel.title)}${star(el, panel)}</h3><div class="empty">nothing recorded ${win ? "in this run" : `in the last ${chosen().label}`}</div>`;
    return;
  }
  const [start, end] = win || rangeWindow();
  const hasRight = series.some((s) => s.axis === "right"), L = 64, R = hasRight ? 56 : 12, T = 8, B = 22;
  const bounds = (axis) => { const on = series.filter((s) => s.axis === axis), v = on.flatMap((s) => s.points.map((p) => p[1]));
    const limits = on.filter((s) => s.limit).flatMap((s) => s.points.map((p) => p[1]));
    return v.length ? [Math.min(0, ...v), axisTop(v, limits)] : [0, 1]; };
  const ax = { left: bounds("left"), right: bounds("right") };
  const X = (t) => L + ((t - start) / (end - start)) * (svgW - L - R);
  const Y = (v, a) => T + (1 - (v - ax[a][0]) / ((ax[a][1] - ax[a][0]) || 1)) * (H - T - B);
  let g = "";
  for (let i = 0; i <= 4; i++) {
    const y = T + (i / 4) * (H - T - B), unitOf = (a) => (series.find((s) => s.axis === a) || {}).unit;
    g += `<line x1="${L}" x2="${svgW - R}" y1="${y}" y2="${y}" stroke="var(--line)"/>`;
    if (i % 2 === 0) {
      g += `<text x="${L - 6}" y="${y + 4}" text-anchor="end">${esc(axisText(ax.left[1] - (i / 4) * (ax.left[1] - ax.left[0]), unitOf("left")))}</text>`;
      if (hasRight) g += `<text x="${svgW - R + 6}" y="${y + 4}">${esc(axisText(ax.right[1] - (i / 4) * (ax.right[1] - ax.right[0]), unitOf("right")))}</text>`;
    }
  }
  const long = end - start > 86400, short = end - start < 600;
  for (let i = 0; i <= 4; i++) {
    const t = start + (i / 4) * (end - start), d = new Date(t * 1000);
    const tl = long ? d.toLocaleDateString(undefined, { weekday: "short", day: "numeric" })
      : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: short ? "2-digit" : undefined });
    g += `<text x="${X(t)}" y="${H - 5}" text-anchor="middle">${esc(tl)}</text>`;
  }
  const colors = series.map((s, i) => (s.axis === "right" ? "var(--heat)" : PALETTE[i % PALETTE.length]));  // the right axis is temperature
  series.forEach((s, i) => {
    const pts = s.points.map((p) => `${X(p[0]).toFixed(1)},${Y(p[1], s.axis).toFixed(1)}`).join(" ");
    g += `<polyline points="${pts}" fill="none" stroke="${colors[i]}" stroke-width="1.6" stroke-linejoin="round"/>`;
  });
  g += `<line class="xhair" y1="${T}" y2="${H - B}" stroke="var(--muted)" stroke-dasharray="3 3" style="display:none"/>`;
  el.plot = { series, colors, start, end, step: stepFor(240, end - start), left: L, right: R, width: svgW };
  const whole = win ? `<span class="muted"> · the whole run, ${esc(duration(end - start))}</span>` : "";
  el.innerHTML = `<h3>${esc(panel.title)}${whole}${star(el, panel)}</h3><svg width="100%" height="${H}" viewBox="0 0 ${svgW} ${H}">${g}</svg>
    <div class="legend">${series.map((s, i) => `<span><i style="background:${colors[i]}"></i>${esc(s.name || panel.title)}</span>`).join("")}</div>
    <div class="tip" hidden></div>`;
  if (el.lastPointer && el.matches(":hover")) hover(el, el.lastPointer);  // a redraw keeps the crosshair
  if (!el.dataset.hover) {  // once per chart box: the box stays, its contents are redrawn
    el.dataset.hover = "1";
    el.addEventListener("mousemove", (ev) => { el.lastPointer = { clientX: ev.clientX, clientY: ev.clientY }; hover(el, ev); });
    el.addEventListener("mouseleave", () => { el.querySelector(".tip").hidden = true; const l = el.querySelector(".xhair"); if (l) l.style.display = "none"; });
  }
}
// ---- tabs: Favourites, then one per kind of chart ----
function storedOrDefault(stored, defaults, mode) {  // your stars as kept, else the defaults
  let list = null;
  try { list = stored ? JSON.parse(stored) : null; } catch (e) { list = null; }
  return Array.isArray(list) ? list : defaults[mode];
}
function favouritesFor(stored, defaults, mode, trainingCharts, charts = null) {  // what Favourites shows now
  return storedOrDefault(stored, defaults, mode)
    .filter((t) => !charts || t in charts)  // a chart this page does not have (renamed, other platform)
    .filter((t) => mode === "training" || !trainingCharts.has(t));  // a fine-tune's charts wait for one
}
const starred = (stored, defaults, mode, title) => toggled(storedOrDefault(stored, defaults, mode), title);  // keeps hidden ones
const toggled = (list, title) => (list.includes(title) ? list.filter((t) => t !== title) : [...list, title]);
function addressFor(params, now) {  // the page's address as it is now: range, theme and tab
  const out = new URLSearchParams(params);
  out.set("range", now.range);
  if (now.theme) out.set("theme", now.theme); else out.delete("theme");
  out.delete("open");  // the old form, replaced by view
  out.set("view", now.view);
  return `?${out}`;
}
function viewFrom(params, keys) {  // ?view=memory; an old ?open=memory,hardware link opens its first
  const asked = (params.get("view") || (params.get("open") || "").split(",")[0] || "").toLowerCase();
  return keys.includes(asked) ? asked : "favourites";
}
function chartHeight(area, n) {  // two to a row (one alone takes the width), sharing the area; a floor, then scroll
  const rows = n <= 1 ? 1 : Math.ceil(n / 2), gap = 10, chrome = 78;  // chrome: a chart's title, legend, padding
  return Math.max(110, Math.round((area - gap * (rows - 1)) / rows - chrome));
}
let view = "favourites";
const trainingCharts = () => new Set(SPEC.views.find((v) => v.run).charts);
const favourites = () => favouritesFor(store.get("iv-favourites"), SPEC.favourites, state.mode, trainingCharts(), SPEC.charts);
function chartsOf(key) { return key === "favourites" ? favourites() : SPEC.views.find((v) => v.key === key).charts; }
function tabs() {
  $("views").innerHTML = SPEC.views.map((v) => `<button role="tab" data-v="${v.key}" class="${v.key === view ? "on" : ""}">${esc(v.label)}</button>`).join("");
  $("views").onclick = (ev) => {
    const k = ev.target.dataset.v;
    if (!k) return;
    view = k;
    document.querySelectorAll("#views button").forEach((b) => b.classList.toggle("on", b.dataset.v === k));
    history.replaceState(null, "", addressFor(new URLSearchParams(location.search),
      { range, theme: document.documentElement.dataset.theme || null, view: k }));
    buildView();
    renderCharts();
  };
  $("fav-reset").onclick = () => { store.set("iv-favourites", ""); buildView(); renderCharts(); };
}
function buildView() {  // the boxes for the chosen tab's charts; renderCharts fills them
  const titles = chartsOf(view), grid = $("grid");
  $("view-hint").textContent = view === "favourites"
    ? `${titles.length} starred · kept in this browser · ☆ a chart in any tab to add it`
    : "☆ adds a chart to Favourites";
  $("fav-reset").hidden = view !== "favourites" || !store.get("iv-favourites");
  grid.className = `grid${titles.length <= 1 ? " one" : ""}`;
  grid.innerHTML = titles.length ? titles.map((t, i) => `<div class="chart" id="c${i}" data-title="${esc(t)}"></div>`).join("")
    : `<div class="empty">No favourites yet: star ☆ a chart in any tab to add it here.</div>`;
  grid.onclick = (ev) => {
    const t = ev.target.dataset && ev.target.dataset.star;
    if (!t) return;
    store.set("iv-favourites", JSON.stringify(starred(store.get("iv-favourites"), SPEC.favourites, state.mode, t)));
    if (view === "favourites") buildView();
    renderCharts();
  };
}
async function renderCharts() {
  await new Promise((done) => requestAnimationFrame(done));  // sizes are known after a frame
  const titles = chartsOf(view), fav = new Set(favourites()), run = trainingCharts();
  const h = chartHeight($("area").clientHeight - 20, titles.length);
  await Promise.all(titles.map(async (t, i) => {
    const el = $(`c${i}`);
    if (!el) return;
    el.dataset.h = h;
    el.dataset.fav = fav.has(t) ? "1" : "";
    await renderChart(el, SPEC.charts[t], run.has(t) ? state.window : null);
  }));
}

// ---- controls and refresh ----
function theme() {
  const saved = asked.get("theme") || store.get("iv-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  $("theme").onclick = () => {
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    const next = (document.documentElement.dataset.theme || (dark ? "dark" : "light")) === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("iv-theme", next);
  };
}
function ranges() {
  $("ranges").innerHTML = SPEC.ranges.map((r) => `<button data-r="${r.key}" class="${r.key === range ? "on" : ""}">${r.label}</button>`).join("");
  $("ranges").onclick = (ev) => {
    const r = ev.target.dataset.r;
    if (!r) return;
    range = r;
    store.set("iv-range", r);
    document.querySelectorAll("#ranges button").forEach((b) => b.classList.toggle("on", b.dataset.r === r));
    refresh(true);
  };
}
function rateBox() {
  $("rate").value = store.get("iv-rate") || "";
  $("currency").value = store.get("iv-currency") || "₹";
  const save = () => { store.set("iv-rate", $("rate").value); store.set("iv-currency", $("currency").value); renderEnergy(); };
  $("rate").oninput = save;
  $("currency").onchange = save;
}
let last = { charts: 0, energy: 0 };
async function refresh(all = false) {
  if (document.hidden && !all) return;  // nothing to draw while the tab is not visible
  asking = new Map();  // a fresh refresh asks again
  const now = Date.now(), was = state.mode;
  await decideMode();
  if (state.mode !== was) { all = true; if (view === "favourites") buildView(); }  // its favourites change with it
  const jobs = [renderRunning(), trainingWindow(), renderBattery()];  // plugging in shows within seconds
  if (all || now - last.energy >= ENERGY_EVERY) { last.energy = now; jobs.push(renderEnergy()); }
  await Promise.all(jobs);
  await renderTiles();  // after energy, which Energy/token uses
  if (all || now - last.charts >= CHARTS_EVERY) { last.charts = now; await renderCharts(); }
  $("updated").textContent = `updated ${new Date().toLocaleTimeString()}`;
}
function useSpec(spec, wanted) {  // the dashboard's description, and a range it offers (1 h if not)
  SPEC = spec;
  range = wanted || range;
  if (!SPEC.ranges.some((r) => r.key === range)) range = "1h";
}
async function main() {
  useSpec(await (await fetch("/api/spec")).json(), asked.get("range"));
  $("repo-link").href = SPEC.repo;
  $("repo-link").textContent = SPEC.repo.replace(/^https:\/\//, "");
  $("version").textContent = `v${SPEC.version}`;
  view = viewFrom(asked, SPEC.views.map((v) => v.key));
  theme(); ranges(); rateBox(); tabs(); buildView();
  await refresh(true);
  setInterval(refresh, TILES_EVERY);
  const tick = () => { $("clock").textContent = clockText(new Date()); };
  tick();
  setInterval(() => !document.hidden && tick(), 1000);
  document.addEventListener("visibilitychange", () => !document.hidden && refresh(true));
  let resize;
  addEventListener("resize", () => { clearTimeout(resize); resize = setTimeout(renderCharts, 200); });
}
if (typeof document !== "undefined") main();
if (typeof module !== "undefined") {
  module.exports = { fmt, text, axisText, compact, niceMax, energyText, tone, nearest, readout, duration, clockText, modeOf, batteryText, leftText, ofText, runWindow,
    favouritesFor, toggled, viewFrom, chartHeight, perToken, runState, starred, addressFor, sharedAsk, TILES, useSpec, axisTop };
}
