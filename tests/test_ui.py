import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest
from helpers import known_metric_names

from inference_vitals import grafana, queries, ui

NAME = re.compile(r"\b(?:vllm:|vitals_|vitals:|macmon_)[A-Za-z0-9_:]+")


def _queries(spec):
    for tile in spec["tiles"]["run"] + spec["tiles"]["machine"]:
        for k, v in tile.items():
            if k.endswith("query"):
                yield tile["title"], v
    for k, v in spec["energy"].items():
        if k.endswith("query"):
            yield "energy", v
    for k, v in spec["running"].items():
        if k.endswith("query"):
            yield "running", v
    for tile in spec["training"]["tiles"]:
        for k, v in tile.items():
            if k.endswith("query"):
                yield tile["title"], v
    for part in ("training_band", "battery", "mode"):
        src = spec["training"]["band"] if part == "training_band" else spec[part]
        for k, v in src.items():
            if k.endswith("query"):
                yield part, v
    for chart in spec["charts"].values():
        for t in chart["targets"]:
            yield chart["title"], t["expr"]


@pytest.mark.parametrize("platform", queries.PLATFORMS)
def test_every_query_on_the_page_uses_a_metric_that_exists_on_that_platform(platform):
    known = known_metric_names()[platform]
    for title, q in _queries(ui.spec(platform)):
        names = set(NAME.findall(q))
        assert names and names <= known, (platform, title, names - known)


@pytest.mark.parametrize("platform", queries.PLATFORMS)
def test_the_page_has_the_same_tiles_and_sections_as_the_grafana_dashboard(platform):
    d = grafana.dashboard(platform)
    grafana_tiles = {p["title"] for p in d["panels"] if p["type"] == "stat" and p["title"] != "Running"}
    page = ui.spec(platform)
    assert {t["title"] for t in page["tiles"]["run"] + page["tiles"]["machine"]} == grafana_tiles
    rows = [p for p in d["panels"] if p["type"] == "row" and p.get("panels")]
    tabs = [v for v in page["views"] if v["key"] != "favourites"]
    assert [v["row"] for v in tabs] == [r["title"] for r in rows]  # one tab per Grafana row, in its order
    shown = {c for v in tabs for c in v["charts"]}
    for (
        r
    ) in rows:  # every Grafana chart is on the page, except the loss chart the page draws with temperature
        assert {p["title"] for p in r["panels"]} - {"Training and validation loss"} <= shown, r["title"]


def test_each_tile_gets_a_gauge_that_suits_its_number():
    kinds = {
        t["title"]: t["kind"]
        for t in ui.spec(queries.MAC)["tiles"]["run"] + ui.spec(queries.MAC)["tiles"]["machine"]
    }
    assert kinds["Progress"] == "progress"
    assert kinds["Writing now"] == kinds["Reading speed"] == "speed"
    assert kinds["TTFT (median)"] == kinds["GPU temp"] == "zones"
    assert kinds["Context used"] == kinds["Memory free"] == "bar"
    assert kinds["GPU mem left"] == "split" and kinds["GPU busy"] == "ring" and kinds["Thermal"] == "steps"
    assert kinds["Answers cut off"] == kinds["Invalid answers"] == "ratio"
    assert kinds["Swap in/out"] == "spark" and kinds["Energy/token"] == "energy-per-token"
    linux = {t["title"]: t["kind"] for t in ui.spec(queries.LINUX)["tiles"]["machine"]}
    assert linux["GPU slowdown"] == "steps"


def test_energy_adds_up_power_over_the_range_and_counts_only_the_ai_above_idle():
    e = ui.spec(queries.MAC)["energy"]
    assert e["meter"] == "whole Mac" and "macmon_sys_power_watts" in e["total_query"]

    def fill(q, range_, step, base=""):  # as the page fills them in
        return q.replace("{range}", range_).replace("{step}", str(step)).replace("{base}", str(base))

    total = fill(e["total_query"], "1h", 15)
    assert total == "sum_over_time((max(macmon_sys_power_watts))[1h:15s]) * 15 / 3600"
    ai = fill(e["ai_query"], "6h", 60, base=12.5)
    assert "- 12.5" in ai and "vitals_current_phase" in ai and "[6h:60s]" in ai and ai.endswith("* 60 / 3600")
    assert e["idle_query"] == queries.IDLE  # recorded once a minute (queries.rules_config)
    assert ui.spec(queries.LINUX)["energy"]["meter"] == "GPUs only"


def test_the_subquery_step_grows_with_the_range_to_stay_cheap():
    ranges = ui.spec(queries.MAC)["ranges"]
    assert [r["key"] for r in ranges] == ["15m", "1h", "6h", "24h", "7d"]
    assert [r["step"] for r in ranges] == [15, 15, 15, 60, 300]
    # no energy sum takes more than ~2,000 steps
    assert all(r["seconds"] // r["step"] <= 2100 for r in ranges)


# The page server: static files, the page's description, and two read-only Prometheus queries.


class _FakePrometheus:
    def __init__(self):
        import http.server

        seen = self.seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append(self.path)
                body = b'{"status":"success","data":{"resultType":"vector","result":[]}}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def page():
    prom = _FakePrometheus()
    server = ui.serve(0, queries.MAC, prom.url)
    yield f"http://127.0.0.1:{server.server_address[1]}", prom
    server.shutdown()
    prom.server.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def test_the_page_its_files_and_its_description_are_served(page):
    url, _ = page
    status, ctype, body = _get(url + "/")
    assert status == 200 and ctype.startswith("text/html") and b"inference-vitals" in body
    assert _get(url + "/page.js")[1].startswith("text/javascript")
    assert _get(url + "/page.css")[1].startswith("text/css")
    assert _get(url + "/favicon.svg")[1].startswith("image/svg+xml")
    spec = json.loads(_get(url + "/api/spec")[2])
    assert spec["platform"] == queries.MAC and spec["repo"] == grafana.REPO


def test_only_two_read_only_prometheus_queries_pass_through_and_only_with_their_own_parameters(page):
    url, prom = page
    q = urllib.parse.urlencode({"query": "up", "time": "1", "extra": "x"})
    assert _get(f"{url}/api/v1/query?{q}")[0] == 200
    r = urllib.parse.urlencode({"query": "up", "start": "1", "end": "2", "step": "15"})
    assert _get(f"{url}/api/v1/query_range?{r}")[0] == 200
    assert prom.seen[0] == "/api/v1/query?query=up&time=1"  # the unknown parameter was dropped
    assert prom.seen[1].startswith("/api/v1/query_range?query=up&start=1&end=2&step=15")
    for path in (
        "/api/v1/admin/tsdb/delete_series",
        "/api/v1/series?match[]=up",
        "/../pyproject.toml",
        "/etc/passwd",
    ):
        with pytest.raises(urllib.error.HTTPError) as err:
            _get(url + path)
        assert err.value.code == 404, path
    assert len(prom.seen) == 2


def test_nothing_can_be_posted(page):
    url, _ = page
    req = urllib.request.Request(url + "/api/v1/query", data=b"query=up", method="POST")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=5)
    assert err.value.code == 405


def test_the_server_listens_on_this_machine_only(page):
    url, _ = page
    assert url.startswith("http://127.0.0.1:")


def test_training_has_its_own_tiles_band_and_chart():
    t = ui.spec(queries.MAC)["training"]
    kinds = {x["title"]: x["kind"] for x in t["tiles"]}
    assert kinds["Progress"] == "progress" and kinds["Learning?"] == "steps"
    assert kinds["Train loss"] == kinds["Val loss"] == "spark" and kinds["Tokens/s"] == "speed"
    learning = next(x for x in t["tiles"] if x["title"] == "Learning?")
    assert learning["names"] == [
        "Learning",
        "Watch",
        "Memorising",
    ]  # in the order training.LEARNING, ... count
    assert t["band"]["names"][1:] == ["Step", "Validate", "Save"]
    loss = ui.spec(queries.MAC)["charts"]["Training and validation loss, against GPU temperature"]
    assert [x["legend"] for x in loss["targets"]][:2] == [
        "train loss {{run_name}}",
        "validation loss {{run_name}}",
    ]


def test_the_page_tells_a_fine_tune_from_a_model_run_by_which_reported_last():
    mode = ui.spec(queries.MAC)["mode"]
    assert "vitals_training_updated_seconds" in mode["training_query"]
    assert "vitals_run_updated_seconds" in mode["run_query"]


def test_the_battery_time_comes_from_the_battery_itself():
    b = ui.spec(queries.LINUX)["battery"]
    assert "vitals_battery_draw_watts" in b["draw_query"] and "vitals_battery_remaining_wh" in b["wh_query"]


def test_a_finished_fine_tune_stays_readable_with_its_last_numbers():
    t = ui.spec(queries.MAC)["training"]
    for tile in t["tiles"]:
        for k, q in tile.items():
            if k.endswith("query") and k != "max_query":  # max_query: the range's top, not the last value
                assert q.startswith("max(last_over_time(vitals_training_"), (tile["title"], k)
    band = t["band"]
    assert "last_over_time(vitals_training_phase[7d])" in band["phase_query"]
    assert "topk(1" in band["phase_query"]  # only the latest run, not every run of the week
    assert "on(run_name, run_id)" in band["phase_query"]  # two runs called "python" are still two runs
    assert "vitals_training_alive_seconds" in band["alive_query"]


def test_every_training_number_is_the_latest_runs_never_an_older_ones():
    t = ui.spec(queries.MAC)["training"]
    latest = "and on(run_name, run_id) topk(1, last_over_time(vitals_training_updated_seconds[7d]))"
    for tile in t["tiles"]:
        for k, q in tile.items():
            if k.endswith("query") and k != "max_query":
                assert latest in q, (tile["title"], k)
    for k, q in t["band"].items():
        if k.endswith("query"):
            assert latest in q, k


def test_on_a_mac_gpu_memory_left_falls_back_to_the_whole_machine_when_no_model_reports_its_limit():
    tile = next(t for t in ui.spec(queries.MAC)["tiles"]["machine"] if t["title"] == "GPU mem left")
    # the model's own figure first; else the shared memory macmon reports (Apple Silicon: GPU and CPU share it)
    assert tile["query"].startswith("min(vitals_gpu_memory_limit_bytes - vitals_peak_memory_bytes) or ")
    assert (
        "macmon_memory_ram_total_bytes" in tile["query"] and "macmon_memory_ram_used_bytes" in tile["query"]
    )
    assert tile["limit_query"] == "max(vitals_gpu_memory_limit_bytes) or max(macmon_memory_ram_total_bytes)"
    assert tile["used_query"] == "max(vitals_peak_memory_bytes) or max(macmon_memory_ram_used_bytes)"
    assert tile["model_query"] == "max(vitals_gpu_memory_limit_bytes)"  # which of the two is shown
    linux = next(t for t in ui.spec(queries.LINUX)["tiles"]["machine"] if t["title"] == "GPU mem left")
    assert linux["query"] == "sum(vitals_gpu_memory_free_bytes)"  # nvidia-smi always knows


def test_training_sits_with_the_model_sections_before_the_machine_ones():
    for platform in queries.PLATFORMS:
        labels = [v["label"] for v in ui.spec(platform)["views"]]
        assert labels == ["Favourites", "Speed", "Context", "Training", "Memory", "Hardware"], labels


def test_moving_training_up_keeps_the_grafana_panel_ids_of_memory_and_hardware():
    d = grafana.dashboard(queries.MAC)
    rows = {p["title"]: p for p in d["panels"] if p["type"] == "row"}
    ids = lambda title: [p["id"] for p in rows[title]["panels"]]
    assert min(ids("Training")) > max(ids("Hardware"))  # numbered after the others, as when it was added last


@pytest.mark.parametrize("platform", queries.PLATFORMS)
def test_every_tab_names_charts_the_page_defines_and_each_chart_is_on_one_tab(platform):
    spec = ui.spec(platform)
    named = [c for v in spec["views"] for c in v["charts"]]
    assert set(named) == set(spec["charts"]) and len(named) == len(set(named))
    speed = next(v for v in spec["views"] if v["key"] == "speed")
    assert speed["charts"][0] == "Writing speed against GPU temperature"  # the old main chart leads Speed
    training = next(v for v in spec["views"] if v["key"] == "training")
    assert (
        training["charts"][0] == "Training and validation loss, against GPU temperature" and training["run"]
    )


@pytest.mark.parametrize("platform", queries.PLATFORMS)
def test_favourites_start_with_charts_that_exist(platform):
    spec = ui.spec(platform)
    fav = spec["favourites"]
    assert fav["inference"][0] == "Writing speed against GPU temperature"
    assert fav["training"][0] == "Training and validation loss, against GPU temperature"
    for mode in ("inference", "training"):
        assert set(fav[mode]) <= set(spec["charts"]), mode


def test_each_per_token_energy_counts_only_its_own_kind_of_work():
    e = ui.spec(queries.MAC)["energy"]
    assert queries.ACTIVE in e["ai_query"]  # the band's AI share: model runs and fine-tunes together
    assert queries.RUN_ACTIVE in e["ai_run_query"] and queries.TRAIN_ACTIVE in e["ai_training_query"]


def test_progress_is_worked_out_on_the_page_from_two_numbers_it_asks_for_anyway():
    for platform in queries.PLATFORMS:
        spec = ui.spec(platform)
        tiles = spec["tiles"]["run"] + spec["training"]["tiles"]
        for tile in (t for t in tiles if t["kind"] == "progress"):
            assert "query" not in tile, tile["title"]  # done / total, divided in the browser
        run_tile = next(t for t in spec["tiles"]["run"] if t["kind"] == "progress")
        assert run_tile["done_query"] == spec["running"]["done_query"]  # one spelling: asked once per refresh
        assert run_tile["total_query"] == spec["running"]["total_query"]


@pytest.mark.parametrize("platform", queries.PLATFORMS)
def test_the_page_and_grafana_colour_each_tile_at_the_same_levels(platform):
    levels = {}
    for p in grafana.dashboard(platform)["panels"]:
        if p["type"] == "stat":
            steps = [
                s["value"]
                for s in p["fieldConfig"]["defaults"]["thresholds"]["steps"]
                if s["value"] is not None
            ]
            levels[p["title"]] = sorted(steps)
    spec = ui.spec(platform)
    for tile in spec["tiles"]["run"] + spec["tiles"]["machine"]:
        if "warn" in tile:
            scale = tile.get("scale", 1)
            assert sorted([tile["warn"] / scale, tile["bad"] / scale]) == levels[tile["title"]], tile["title"]


def test_state_names_come_from_the_code_that_defines_the_states():
    from inference_vitals import metrics, training

    spec = ui.spec(queries.MAC)
    assert spec["running"]["names"] == queries.PHASE_NAMES
    band = spec["training"]["band"]
    assert len(band["names"]) == len(training.PHASES) and len(band["parts"]) == len(training.PARTS) + 1
    learning = next(t for t in spec["training"]["tiles"] if t["title"] == "Learning?")
    assert len(learning["names"]) == 3 and learning["names"][training.MEMORISING] == "Memorising"
    assert queries.PHASE_NAMES[metrics.WRITING] == "Writing (decode)"
