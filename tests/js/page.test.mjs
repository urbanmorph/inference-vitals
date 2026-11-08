// The page's pure helpers, run with `node --test` (pytest runs this file too, see test_page_js.py).
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const page = createRequire(import.meta.url)("../../src/inference_vitals/page/page.js");

test("numbers keep their units and stay short", () => {
  const { text } = page;
  assert.equal(text(21.04e9, "bytes"), "21.0 GB");
  assert.equal(text(18000, "Bps"), "18 kB/s");
  assert.equal(text(0.228, "percentunit"), "22.8 %");
  assert.equal(text(76.83, "celsius"), "76.8 °C");
  assert.equal(text(16.4, "suffix: tok/s"), "16.4 tok/s");
  assert.equal(text(369138, "suffix: tok/s"), "369.1k tok/s");
  assert.equal(text(null, "watt"), "–");
});

test("axis labels are compact so they fit the margin", () => {
  assert.equal(page.axisText(6000, "rpm"), "6k rpm");
  assert.equal(page.axisText(45, "watt"), "45.0 W");
  assert.equal(page.axisText(2.5e9, "bytes"), "2.50 GB");
});

test("axis tops round up to 1, 2, 2.5 or 5 times a power of ten", () => {
  assert.deepEqual([100, 110, 24.6, 68.8, 6000, 0.5].map(page.niceMax), [100, 200, 25, 100, 10000, 0.5]);
});

test("energy switches to kWh past a thousand Wh", () => {
  assert.equal(page.energyText(29.94), "29.9 Wh");
  assert.equal(page.energyText(1420), "1.42 kWh");
  assert.equal(page.energyText(null), "–");
});

test("colours follow the thresholds, either way round", () => {
  assert.equal(page.tone(80, { warn: 85, bad: 95 }), "ok");
  assert.equal(page.tone(90, { warn: 85, bad: 95 }), "warn");
  assert.equal(page.tone(10, { warn: 30, bad: 15, low_is_bad: true }), "bad");
  assert.equal(page.tone(10, {}), "plain");
});

test("the crosshair reads each line's value nearest the pointer, and skips gaps", () => {
  const pts = [[100, 1], [115, 2], [130, 3], [400, 9]];
  assert.equal(page.nearest(pts, 118), 1);
  assert.equal(page.nearest(pts, 124), 2);
  assert.equal(page.nearest(pts, 5000), 3);
  const series = [{ name: "tokens/s", unit: "suffix: tok/s", points: pts }, { name: "GPU °C", unit: "celsius", points: [[110, 70]] }];
  assert.deepEqual(page.readout(series, 118, 20), [
    { name: "tokens/s", value: "2.00 tok/s", index: 0 },
    { name: "GPU °C", value: "70.0 °C", index: 1 },
  ]);
  assert.deepEqual(page.readout(series, 250, 20), []);  // nothing recorded near that moment
});

test("time left reads in hours and minutes", () => {
  assert.equal(page.duration(1680), "28 min");
  assert.equal(page.duration(3900), "1 h 5 min");
  assert.equal(page.duration(null), "");
  assert.equal(page.duration(20), "under a minute");  // not "0 min"
  assert.equal(page.leftText(20), "under a minute left");
  assert.equal(page.leftText(1680), "about 28 min left");
});

test("the clock shows local time with its time zone", () => {
  const at = new Date(Date.UTC(2026, 10, 7, 17, 31, 5));
  assert.equal(page.clockText(at, "en-IN", "Asia/Kolkata"), "23:01:05 IST");
  assert.equal(page.clockText(at, "en-GB", "Europe/London"), "17:31:05 GMT");
});

test("the page shows whichever reported last, a fine-tune or a model run", () => {
  const { modeOf } = page;
  assert.equal(modeOf(2000, 1000), "training");
  assert.equal(modeOf(1000, 2000), "inference");
  assert.equal(modeOf(1000, null), "training");
  assert.equal(modeOf(null, null), "inference");  // nothing yet: today's page
});

test("the battery chip shows on any laptop: on power its charge, on battery the time left at this load", () => {
  const { batteryText } = page;
  assert.equal(batteryText({ percent: 64, wh: 49.6, draw: 24.8, onPower: 0 }, null).text, "🔋 64% · ~2 h 0 min at this load");
  const plugged = batteryText({ percent: 87, wh: 71, draw: null, onPower: 1 }, 600);
  assert.equal(plugged.hidden, false);
  assert.equal(plugged.text, "🔌 87% · on power");
  assert.equal(plugged.warn, false);  // on power, a run never outlasts it
  assert.equal(batteryText({ percent: null }, null).hidden, true);  // no battery
  assert.equal(batteryText({ percent: 80, wh: 40, draw: null, onPower: 0 }, null).text, "🔋 80%");  // no rate yet
  const short = batteryText({ percent: 18, wh: 6, draw: 60, onPower: 0 }, 9 * 60);  // 6 min left, the run needs 9
  assert.equal(short.warn, true);
  assert.equal(short.text, "🔋 18% · ~6 min at this load (the run needs ~9 min)");
  assert.equal(batteryText({ percent: 64, wh: 49.6, draw: 24.8, onPower: 0 }, 600).warn, false);
  assert.equal(batteryText({ percent: 2, wh: 0.1, draw: 20, onPower: 0 }, null).text, "🔋 2% · under a minute at this load");
});

test("a learning rate and other tiny numbers keep their digits", () => {
  assert.equal(page.text(0.0001, "none"), "1.0e-4");
  assert.equal(page.text(0.42, "none"), "0.42");
});

test("a memory note stays short enough for a tile", () => {
  assert.equal(page.ofText(43.9e9, 51.5e9, ", machine"), "43.9 of 51.5 GB, machine");
  assert.equal(page.ofText(800e6, 51.5e9, " in use"), "800 MB of 51.5 GB in use");  // units differ: both shown
});

test("training charts span the run, with a little room either side, whatever the range buttons say", () => {
  const { runWindow } = page;
  assert.deepEqual(runWindow(1000, 1600, false, 9999), [970, 1630]);  // finished: start to end, 5% either side
  assert.deepEqual(runWindow(1000, 1600, true, 2000), [950, 2050]);  // running: start to now
  assert.equal(runWindow(null, 1600, false, 2000), null);  // nothing to fit: the chosen range
});

test("favourites: your own stars, else the defaults; a fine-tune's charts only while one is shown", () => {
  const { favouritesFor } = page;
  const defaults = { inference: ["speed", "memory"], training: ["loss", "speed", "memory"] };
  const training = new Set(["loss", "tps"]);
  assert.deepEqual(favouritesFor(null, defaults, "inference", training), ["speed", "memory"]);
  assert.deepEqual(favouritesFor(null, defaults, "training", training), ["loss", "speed", "memory"]);
  assert.deepEqual(favouritesFor('["tps","swap"]', defaults, "inference", training), ["swap"]);  // no fine-tune: its chart waits
  assert.deepEqual(favouritesFor('["tps","swap"]', defaults, "training", training), ["tps", "swap"]);
  assert.deepEqual(favouritesFor("not json", defaults, "inference", training), ["speed", "memory"]);  // a broken store: defaults
});

test("a star adds a chart to favourites, and a second click takes it off", () => {
  const { toggled } = page;
  assert.deepEqual(toggled(["a"], "b"), ["a", "b"]);
  assert.deepEqual(toggled(["a", "b"], "a"), ["b"]);
});

test("the address picks the tab, and old ?open= links still land on theirs", () => {
  const { viewFrom } = page;
  const keys = ["favourites", "speed", "memory"];
  assert.equal(viewFrom(new URLSearchParams("view=memory"), keys), "memory");
  assert.equal(viewFrom(new URLSearchParams("open=memory,hardware"), keys), "memory");
  assert.equal(viewFrom(new URLSearchParams("view=nope"), keys), "favourites");
  assert.equal(viewFrom(new URLSearchParams(""), keys), "favourites");
});

test("charts share the space under the tiles, two to a row, never too small", () => {
  const { chartHeight } = page;
  assert.equal(chartHeight(500, 1), 422);  // one chart: the whole area, less its title and legend
  assert.equal(chartHeight(500, 4), 167);  // two rows of two
  assert.equal(chartHeight(500, 12), 110);  // many: a floor, and the area scrolls
});

test("energy per token divides the energy of that kind of work by its own tokens", () => {
  const { perToken } = page;
  // 1 Wh answering over 1,000 tokens = 3.6 J each; the fine-tune's energy is not in it
  assert.equal(perToken({ aiRun: 1, aiTraining: 5, tokens: 1000, trained: 2000 }, "inference"), 3.6);
  assert.equal(perToken({ aiRun: 1, aiTraining: 5, tokens: 1000, trained: 2000 }, "training"), 9);
  assert.equal(perToken({ aiRun: null, tokens: 1000 }, "inference"), null);
  assert.equal(perToken({ aiRun: 1, tokens: 0 }, "inference"), null);
});

test("a run counts as training only while it is still being read; a killed one is stale", () => {
  const { runState } = page;
  assert.equal(runState(1, 1000, 1030), "training");
  assert.equal(runState(1, 1000, 1200), "stale");  // phase says step, but nothing has read it for 200 s
  assert.equal(runState(0, 1000, 1200), "finished");
  assert.equal(runState(null, null, 1200), "none");
});

test("while the model loads there is a start but no update yet: the window runs to now", () => {
  assert.deepEqual(page.runWindow(1000, null, false, 1600), [970, 1630]);
});

test("favourites never name a chart this page does not have, and starring keeps your training charts", () => {
  const { favouritesFor, starred } = page;
  const charts = { speed: 1, loss: 1 };
  const d = { inference: ["speed"], training: ["loss", "speed"] };
  assert.deepEqual(favouritesFor('["gone","speed"]', d, "inference", new Set(["loss"]), charts), ["speed"]);
  // in a model run, starring "speed" off must not drop the stored training chart "loss"
  assert.deepEqual(starred('["loss","speed"]', d, "inference", "speed"), ["loss"]);
});

test("the address written on a tab click has the current range, theme and tab", () => {
  const { addressFor } = page;
  assert.equal(addressFor(new URLSearchParams("range=1h&x=1"), { range: "7d", theme: "dark", view: "memory" }),
    "?range=7d&x=1&theme=dark&view=memory");
  assert.equal(addressFor(new URLSearchParams(""), { range: "1h", theme: null, view: "favourites" }), "?range=1h&view=favourites");
});

test("a query asked twice in one refresh goes out once", async () => {
  const { sharedAsk } = page;
  const cache = new Map();
  let calls = 0;
  const ask = async () => { calls += 1; return 7; };
  const [a, b] = await Promise.all([sharedAsk(cache, "q", ask), sharedAsk(cache, "q", ask)]);
  assert.equal(a, 7); assert.equal(b, 7); assert.equal(calls, 1);
});

test("every tile renderer shows its tile's name, even before any number has arrived", async () => {
  const { TILES, useSpec } = page;
  useSpec({ ranges: [{ key: "1h", label: "1 h", seconds: 3600, step: 15 }] });
  const tile = { title: "A tile", unit: "none", names: ["One", "Two"], min: 0, max: 10, warn: 5, bad: 8, query: "q",
                 done_query: "d", total_query: "t", max_query: "m", used_query: "u", limit_query: "l", of_query: "o" };
  for (const [kind, render] of Object.entries(TILES)) {
    const html = await render(tile, null, null, "plain");
    assert.match(html, /A tile/, kind);
  }
});
