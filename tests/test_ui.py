import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest
from test_monitor import _known_metric_names

from inference_vitals import monitor, ui

NAME = re.compile(r"\b(?:vllm:|vitals_|macmon_)[A-Za-z0-9_:]+")


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
    for chart in [spec["main"], *(p for s in spec["sections"] for p in s["panels"])]:
        for t in chart["targets"]:
            yield chart["title"], t["expr"]


@pytest.mark.parametrize("platform", monitor.PLATFORMS)
def test_every_query_on_the_page_uses_a_metric_that_exists_on_that_platform(platform):
    known = _known_metric_names()[platform]
    for title, q in _queries(ui.spec(platform)):
        names = set(NAME.findall(q))
        assert names and names <= known, (platform, title, names - known)


@pytest.mark.parametrize("platform", monitor.PLATFORMS)
def test_the_page_has_the_same_tiles_and_sections_as_the_grafana_dashboard(platform):
    d = monitor.dashboard(platform)
    grafana_tiles = {p["title"] for p in d["panels"] if p["type"] == "stat" and p["title"] != "Running"}
    page = ui.spec(platform)
    assert {t["title"] for t in page["tiles"]["run"] + page["tiles"]["machine"]} == grafana_tiles
    rows = [p for p in d["panels"] if p["type"] == "row" and p.get("panels")]
    assert [s["title"] for s in page["sections"]] == [r["title"] for r in rows]
    assert [len(s["panels"]) for s in page["sections"]] == [len(r["panels"]) for r in rows]


def test_each_tile_gets_a_gauge_that_suits_its_number():
    kinds = {
        t["title"]: t["kind"]
        for t in ui.spec(monitor.MAC)["tiles"]["run"] + ui.spec(monitor.MAC)["tiles"]["machine"]
    }
    assert kinds["Progress"] == "progress"
    assert kinds["Writing now"] == kinds["Reading speed"] == "speed"
    assert kinds["TTFT (median)"] == kinds["GPU temp"] == "zones"
    assert kinds["Context used"] == kinds["Memory free"] == "bar"
    assert kinds["GPU mem left"] == "split" and kinds["GPU busy"] == "ring" and kinds["Thermal"] == "steps"
    assert kinds["Answers cut off"] == kinds["Invalid answers"] == "ratio"
    assert kinds["Swap in/out"] == "spark" and kinds["Energy/token"] == "energy-per-token"
    linux = {t["title"]: t["kind"] for t in ui.spec(monitor.LINUX)["tiles"]["machine"]}
    assert linux["GPU slowdown"] == "steps"


def test_energy_adds_up_power_over_the_range_and_counts_only_the_ai_above_idle():
    e = ui.spec(monitor.MAC)["energy"]
    assert e["meter"] == "whole Mac" and "macmon_sys_power_watts" in e["total_query"]

    def fill(q, range_, step, base=""):  # as the page fills them in
        return q.replace("{range}", range_).replace("{step}", str(step)).replace("{base}", str(base))

    total = fill(e["total_query"], "1h", 15)
    assert total == "sum_over_time((max(macmon_sys_power_watts))[1h:15s]) * 15 / 3600"
    ai = fill(e["ai_query"], "6h", 60, base=12.5)
    assert "- 12.5" in ai and "vitals_current_phase" in ai and "[6h:60s]" in ai and ai.endswith("* 60 / 3600")
    assert "quantile_over_time(0.1" in e["idle_query"]
    assert ui.spec(monitor.LINUX)["energy"]["meter"] == "GPUs only"


def test_the_subquery_step_grows_with_the_range_to_stay_cheap():
    ranges = ui.spec(monitor.MAC)["ranges"]
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
    server = ui.serve(0, monitor.MAC, prom.url)
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
    assert spec["platform"] == monitor.MAC and spec["repo"] == monitor.REPO


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
