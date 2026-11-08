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

test("the battery chip shows only on battery, with the time left at this load", () => {
  const { batteryText } = page;
  assert.equal(batteryText({ percent: 64, wh: 49.6, draw: 24.8, onPower: 0 }, null).text, "🔋 64% · ~2 h 0 min at this load");
  assert.equal(batteryText({ percent: 64, wh: 49.6, draw: 24.8, onPower: 1 }, null).hidden, true);  // on power
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
