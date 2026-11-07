// inference-vitals' page. Everything it shows is described by /api/spec; numbers come from Prometheus
// through the page server's two read-only routes. Nothing is fetched from anywhere else.
"use strict";

const TILES_EVERY = 5000, CHARTS_EVERY = 30000, ENERGY_EVERY = 60000;
const $ = (id) => document.getElementById(id);
const store = {  // per-browser conveniences; the page works without them
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private window */ } },
};
// ?theme=, ?range= and ?open= (sections to show opened), for links and screenshots
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
async function one(query) {
  const r = await instant(query);
  const v = r.length ? parseFloat(r[0].value[1]) : NaN;
  return Number.isFinite(v) ? v : null;
}
const stepFor = (points) => Math.max(5, Math.round(chosen().seconds / points));
function rangeQuery(query, points = 240) {
  const end = Date.now() / 1000, span = chosen().seconds;
  return prom("/api/v1/query_range", { query, start: end - span, end, step: stepFor(points) }).catch(() => []);
}
const fill = (q) => q.replaceAll("{range}", range);
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
  if (v === null || v === undefined || !Number.isFinite(v)) return { n: "–", u: "" };
  const d = (x) => (digits !== undefined ? digits : Math.abs(x) >= 100 ? 0 : Math.abs(x) >= 10 ? 1 : 2);
  const scaled = (x, steps) => { let i = 0; while (Math.abs(x) >= 1000 && i < steps.length - 1) { x /= 1000; i++; } return [x, steps[i]]; };
  switch (unit) {
    case "bytes": case "decbytes": { const [x, u] = scaled(v, ["B", "kB", "MB", "GB", "TB"]); return { n: x.toFixed(d(x)), u }; }
    case "Bps": { const [x, u] = scaled(v, ["B/s", "kB/s", "MB/s", "GB/s"]); return { n: x.toFixed(x < 10 && u !== "B/s" ? 1 : 0), u }; }
    case "percentunit": return { n: (v * 100).toFixed(d(v * 100)), u: "%" };
    case "percent": return { n: v.toFixed(d(v)), u: "%" };
    case "watt": return { n: v.toFixed(d(v)), u: "W" };
    case "celsius": return { n: v.toFixed(1), u: "°C" };
    case "none": case undefined: case "": return { n: compact(v, d), u: "" };
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
function niceMax(v) {  // the next round number at or above v: 1, 2, 2.5 or 5 times a power of ten
  if (!v || v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((x) => x >= v * 0.999);
}

// ---- tiles ----
const head = (title, v, unit, t, digits, shown) => { const f = shown === undefined ? fmt(v, unit, digits) : { n: shown, u: "" };
  return `<div class="name">${esc(title)}</div><div class="value ${t}">${esc(f.n)}<span class="unit">${esc(f.u)}</span></div>`; };
const sub = (s) => `<div class="sub">${esc(s || "")}</div>`;

async function renderTile(t) {
  const v = t.query ? await one(fill(t.query)) : null;
  const scaled = v === null ? null : v * (t.scale || 1);
  const tn = tone(scaled, t);
  switch (t.kind) {
    case "progress": {
      const [done, total] = await Promise.all([one(t.done_query), one(t.total_query)]);
      return head(t.title, scaled === null ? null : scaled * 100, "%", scaled === null ? "plain" : "ok", 0)
        + sub(total ? `${done} of ${total} pages` : "no run reporting") + bar(scaled * 100, "ok");
    }
    case "speed": {
      const top = await slow(`${range} ${t.max_query}`, () => one(fill(t.max_query))), max = niceMax(Math.max(top || 0, v || 0));
      const f = fmt(v, t.unit);
      return `<div class="name">${esc(t.title)}</div>${arc(v, 0, max, f.n, f.u || t.unit, v === null || t.unit === "W" ? "plain" : "ok")}`
        + `<div class="sub center">${top === null ? "" : `top in range ${text(top, t.unit)}`}</div>`;
    }
    case "zones":
      return `<div class="name">${esc(t.title)}</div>`
        + arc(v, t.min, t.max, fmt(v, t.unit).n, t.unit, tn, [[t.min, t.warn, "ok"], [t.warn, t.bad, "warn"], [t.bad, t.max, "bad"]]);
    case "bar": {
      let note = "";
      if (t.used_query) {
        const [used, limit] = await Promise.all([one(t.used_query), one(t.limit_query)]);
        if (used !== null && limit) note = `${Math.round(used).toLocaleString()} of ${Math.round(limit).toLocaleString()} tokens`;
      }
      return head(t.title, scaled, t.unit, tn) + sub(note) + bar(scaled, tn, t.ticks);
    }
    case "split": {
      const [used, limit] = await Promise.all([one(t.used_query), one(t.limit_query)]);
      return head(t.title, v, t.unit, tn) + sub(used !== null && limit ? `${text(used, "bytes")} of ${text(limit, "bytes")} in use` : "")
        + bar(limit ? (used / limit) * 100 : 0, tn, [], "var(--muted)");
    }
    case "ring":
      return `<div class="name">${esc(t.title)}</div>${ring(v, fmt(scaled, "").n, "%", v === null ? "plain" : "ok")}`;
    case "steps": {
      const i = v === null ? null : Math.round(v);
      return head(t.title, null, "", stepTone(i), 0, i === null ? "–" : t.names[i] || "–") + steps(t.names, i);
    }
    case "ratio": {
      const of = await one(t.of_query);
      return head(t.title, v, "", tn, 0) + sub(of ? `${v ?? 0} of ${of} pages` : "") + bar(of ? Math.max(v ? 2 : 0, (v / of) * 100) : 0, tn);
    }
    case "spark": {
      const series = await slow(`${range} ${t.query}`, () => rangeQuery(t.query, 60));
      const points = series.length ? series[0].values.map(([x, y]) => [x, parseFloat(y)]) : [];
      return head(t.title, v, t.unit, tn) + spark(points);
    }
    case "energy-per-token": {
      const e = state.energy, jt = e && e.ai !== null && e.tokens ? (e.ai * 3600) / e.tokens : null;
      return head(t.title, jt, t.unit, "plain") + sub(jt === null ? "AI energy ÷ tokens, in range" : `AI energy ÷ ${Math.round(e.tokens).toLocaleString()} tokens`);
    }
    default:
      return head(t.title, v, t.unit, "plain") + sub(t.note);
  }
}
async function renderTiles() {
  await Promise.all(["run", "machine"].map(async (group) => {
    const html = await Promise.all(SPEC.tiles[group].map(async (t) => {
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

// ---- running band and energy ----
const state = { energy: null };
function duration(s) {
  if (s === null || !Number.isFinite(s)) return "";
  const m = Math.round(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}
async function renderRunning() {
  const r = SPEC.running, phases = await instant(r.phase_query);
  $("models").innerHTML = phases.length
    ? phases.map((p) => { const i = Math.round(parseFloat(p.value[1]));
        return `<span class="model">${esc(p.metric.model_name || "model")} <span class="phase">${i > 0 ? '<span class="dot"></span>' : ""}${esc(r.names[i] || "")}</span></span>`; }).join("")
    : `<span class="idle">Nothing reporting. The machine numbers below still update.</span>`;
  const [done, total, perPage] = await Promise.all([one(r.done_query), one(r.total_query), one(r.seconds_per_page_query)]);
  const left = total && done !== null && perPage ? (total - done) * perPage : null;
  $("run-meta").textContent = total ? `page ${done} of ${total}${left ? ` · about ${duration(left)} left` : ""}` : "";
}
async function renderEnergy() {
  const e = SPEC.energy, step = chosen().step;
  const filled = (q, base) => q.replaceAll("{range}", range).replaceAll("{step}", step).replaceAll("{base}", base);
  const base = await one(e.idle_query);
  const [total, ai, tokens] = await Promise.all([
    one(filled(e.total_query)), base === null ? null : one(filled(e.ai_query, base)), one(filled(e.tokens_query)),
  ]);
  state.energy = { total, ai, tokens };
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
const PALETTE = ["var(--accent)", "var(--heat)", "#58a6ff", "#bc8cff", "#e3b341", "#39c5cf", "var(--muted)"];
async function renderChart(el, panel) {
  const series = [];
  await Promise.all(panel.targets.map(async (t, ti) => {
    for (const s of await rangeQuery(t.expr)) {
      series.push({ order: ti, axis: t.axis || "left", unit: t.unit || panel.unit, name: label(t.legend, s.metric),
        points: s.values.map(([x, y]) => [x, parseFloat(y)]).filter((p) => Number.isFinite(p[1])) });
    }
  }));
  series.sort((a, b) => a.order - b.order);
  const svgW = el.clientWidth || 600, H = el.dataset.h ? +el.dataset.h : 180;
  if (!series.some((s) => s.points.length)) {
    el.innerHTML = `<h3>${esc(panel.title)}</h3><div class="empty">nothing recorded in the last ${chosen().label}</div>`;
    return;
  }
  const end = Date.now() / 1000, start = end - chosen().seconds;
  const hasRight = series.some((s) => s.axis === "right"), L = 64, R = hasRight ? 56 : 12, T = 8, B = 22;
  const bounds = (axis) => { const v = series.filter((s) => s.axis === axis).flatMap((s) => s.points.map((p) => p[1]));
    return v.length ? [Math.min(0, ...v), niceMax(Math.max(...v))] : [0, 1]; };
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
  const long = chosen().seconds > 86400;
  for (let i = 0; i <= 4; i++) {
    const t = start + (i / 4) * (end - start), d = new Date(t * 1000);
    const tl = long ? d.toLocaleDateString(undefined, { weekday: "short", day: "numeric" }) : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    g += `<text x="${X(t)}" y="${H - 5}" text-anchor="middle">${esc(tl)}</text>`;
  }
  const colors = series.map((s, i) => PALETTE[(s.axis === "right" && series.length === 2) ? 1 : i % PALETTE.length]);
  series.forEach((s, i) => {
    const pts = s.points.map((p) => `${X(p[0]).toFixed(1)},${Y(p[1], s.axis).toFixed(1)}`).join(" ");
    g += `<polyline points="${pts}" fill="none" stroke="${colors[i]}" stroke-width="1.6" stroke-linejoin="round"/>`;
  });
  g += `<line class="xhair" y1="${T}" y2="${H - B}" stroke="var(--muted)" stroke-dasharray="3 3" style="display:none"/>`;
  el.plot = { series, colors, start, end, step: stepFor(240), left: L, right: R, width: svgW };
  el.innerHTML = `<h3>${esc(panel.title)}</h3><svg width="100%" height="${H}" viewBox="0 0 ${svgW} ${H}">${g}</svg>
    <div class="legend">${series.map((s, i) => `<span><i style="background:${colors[i]}"></i>${esc(s.name || panel.title)}</span>`).join("")}</div>
    <div class="tip" hidden></div>`;
  if (el.lastPointer && el.matches(":hover")) hover(el, el.lastPointer);  // a redraw keeps the crosshair
  if (!el.dataset.hover) {  // once per chart box: the box stays, its contents are redrawn
    el.dataset.hover = "1";
    el.addEventListener("mousemove", (ev) => { el.lastPointer = { clientX: ev.clientX, clientY: ev.clientY }; hover(el, ev); });
    el.addEventListener("mouseleave", () => { el.querySelector(".tip").hidden = true; const l = el.querySelector(".xhair"); if (l) l.style.display = "none"; });
  }
}
function buildSections() {
  $("sections").innerHTML = SPEC.sections.map((s, i) => `<details data-i="${i}"><summary>${esc(s.title)}</summary>
    <div class="grid2">${s.panels.map((_, k) => `<div class="chart" id="p${i}-${k}" data-h="170"></div>`).join("")}</div></details>`).join("");
  const open = (asked.get("open") || "").toLowerCase().split(",");
  document.querySelectorAll("details").forEach((d) => {
    d.addEventListener("toggle", () => d.open && renderSection(+d.dataset.i));
    if (open.includes(SPEC.sections[+d.dataset.i].title.toLowerCase())) d.open = true;
  });
}
async function renderSection(i) {
  await new Promise((done) => requestAnimationFrame(done));  // a just-opened section has its width after a frame
  await Promise.all(SPEC.sections[i].panels.map((p, k) => renderChart($(`p${i}-${k}`), p)));
}
async function renderCharts() {
  $("main").dataset.h = 220;
  await renderChart($("main"), SPEC.main);
  await Promise.all([...document.querySelectorAll("details[open]")].map((d) => renderSection(+d.dataset.i)));
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
  const now = Date.now();
  const jobs = [renderRunning()];
  if (all || now - last.energy >= ENERGY_EVERY) { last.energy = now; jobs.push(renderEnergy()); }
  await Promise.all(jobs);
  await renderTiles();  // after energy, which Energy/token uses
  if (all || now - last.charts >= CHARTS_EVERY) { last.charts = now; await renderCharts(); }
  $("updated").textContent = `updated ${new Date().toLocaleTimeString()}`;
}
async function main() {
  SPEC = await (await fetch("/api/spec")).json();
  range = asked.get("range") || range;
  if (!SPEC.ranges.some((r) => r.key === range)) range = "1h";
  $("repo").href = SPEC.repo;
  $("repo-link").href = SPEC.repo;
  $("repo-link").textContent = SPEC.repo.replace(/^https:\/\//, "");
  theme(); ranges(); rateBox(); buildSections();
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
  module.exports = { fmt, text, axisText, compact, niceMax, energyText, tone, nearest, readout, duration, clockText };
}
