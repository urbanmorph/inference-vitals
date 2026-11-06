import json
import re

from inference_vitals import metrics, monitor, sysstat


def _known_metric_names():
    m = metrics.Metrics(total=1, kv_per_token=1, labels={"model_name": "x"})
    m.started({})
    m.progress(tokens=1, writing_seconds=1.0)
    m.finished(
        {
            "ttft_s": 1.0,
            "seconds": 2.0,
            "prompt_tokens": 1,
            "cached_tokens": 0,
            "generation_tokens": 1,
            "prompt_tps": 1.0,
            "generation_tps": 1.0,
            "active_memory_gb": 1.0,
            "peak_memory_gb": 1.0,
            "cache_memory_gb": 1.0,
            "warm": False,
            "thermal_warning": 0,
            "cpu_speed_limit": 100,
            "finish_reason": "length",
            "parse_error": "x",
            "over_context": True,
            "context_used_tokens": 1,
            "context_limit_tokens": 1,
            "gpu_memory_limit_gb": 1.0,
            "load_s": 1.0,
        }
    )
    ours = {line.split("{")[0].split(" ")[0] for line in m.render().splitlines()}
    return ours | set(monitor.MACMON_METRICS) | set(sysstat.METRICS)


def _all_panels(dashboard):
    for panel in dashboard["panels"]:
        yield panel
        yield from panel.get("panels", [])


def _exprs(dashboard):
    for panel in _all_panels(dashboard):
        for t in panel.get("targets", []):
            yield panel["title"], t["expr"]


def test_every_dashboard_query_uses_a_metric_that_exists():
    known = _known_metric_names()
    for title, expr in _exprs(monitor.dashboard()):
        names = set(re.findall(r"\b(?:vllm:|vitals_|macmon_)[A-Za-z0-9_:]+", expr))
        assert names, (title, expr)
        assert names <= known, (title, names - known)


def test_dashboard_panels_point_at_our_prometheus_and_are_json():
    d = json.loads(json.dumps(monitor.dashboard()))
    assert d["uid"] == "inference-vitals" and d["refresh"] == "10s"
    for p in _all_panels(d):
        if p["type"] != "row":
            assert p["datasource"]["uid"] == monitor.DATASOURCE_UID


def test_everything_listens_on_localhost_only():
    prom = monitor.prometheus_config()
    assert "127.0.0.1:8400" in prom and "127.0.0.1:9091" in prom
    ini = monitor.grafana_ini(monitor.STATE, "pw")
    assert "http_addr = 127.0.0.1" in ini
    assert "reporting_enabled = false" in ini and "check_for_updates = false" in ini
    cmds = monitor.commands(monitor.STATE)
    assert cmds["macmon"][-4:] == ["--host", "127.0.0.1", "--port", "9091"]
    assert "--web.listen-address=127.0.0.1:9090" in cmds["prometheus"]
    assert cmds["sysstat"][-2:] == ["--port", "9092"] and "127.0.0.1:9092" in prom


def test_overview_fits_one_screen_and_the_details_start_collapsed():
    d = monitor.dashboard()
    top = d["panels"]
    assert top[0]["type"] == "row" and top[0]["title"] == "At a glance" and not top[0]["collapsed"]
    first_detail = next(i for i, p in enumerate(top) if p["type"] == "row" and i > 0)
    overview = top[1:first_detail]
    assert max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in overview) <= 20  # about one laptop screen
    details = top[first_detail:]
    assert details and all(p["type"] == "row" and p["collapsed"] and p["panels"] for p in details)


def test_overview_has_the_numbers_that_matter():
    d = monitor.dashboard()
    end = next(i for i, p in enumerate(d["panels"]) if p["type"] == "row" and i > 0)
    titles = {p["title"] for p in d["panels"][1:end]}
    for want in (
        "Progress",
        "Writing now",
        "TTFT (median)",
        "Context used",
        "Answers cut off",
        "Invalid answers",
        "GPU mem left",
        "Memory free",
        "Swap in/out",
        "GPU busy",
        "GPU temp",
        "Thermal",
    ):
        assert want in titles, want


def test_overview_titles_fit_a_narrow_tile_and_every_panel_has_a_unique_id():
    d = monitor.dashboard()
    end = next(i for i, p in enumerate(d["panels"]) if p["type"] == "row" and i > 0)
    for p in d["panels"][1:end]:
        if p["type"] == "stat":
            assert len(p["title"]) <= 15, p["title"]  # about what a 3-column tile shows untruncated
    ids = [p["id"] for p in _all_panels(d)]
    assert len(ids) == len(set(ids))


def test_an_empty_tile_shows_a_neutral_dash_not_a_red_no_data():
    for p in _all_panels(monitor.dashboard()):
        if p["type"] == "stat":
            d = p["fieldConfig"]["defaults"]
            assert d["noValue"] == "–"
            assert {
                "type": "special",
                "options": {"match": "null", "result": {"text": "–", "color": "text"}},
            } in d["mappings"]


def test_the_thermal_tile_is_live_and_named():
    tile = next(p for p in monitor.dashboard()["panels"] if p.get("title") == "Thermal")
    assert tile["targets"][0]["expr"] == "vitals_mac_thermal_state"
    names = {
        m["options"][str(v)]["text"]
        for m in tile["fieldConfig"]["defaults"]["mappings"]
        if m["type"] == "value"
        for v in range(4)
    }
    assert names == {"Nominal", "Fair", "Serious", "Critical"}


def test_tiles_show_plain_numbers_with_no_graph_behind_them_and_name_each_model():
    tiles = [p for p in monitor.dashboard()["panels"] if p.get("type") == "stat"]
    assert len(tiles) == 16
    for t in tiles:
        assert t["options"]["graphMode"] == "none", t["title"]
        assert t["options"]["textMode"] == "auto", t["title"]  # a name above each number when two models show


def test_every_tile_has_a_small_trend_strip_right_under_it():
    panels = monitor.dashboard()["panels"]
    tiles = [p for p in panels if p["type"] == "stat"]
    strips = {(p["gridPos"]["x"], p["gridPos"]["y"]): p for p in panels if p.get("title") == ""}
    assert len(strips) == len(tiles) == 16
    for t in tiles:
        g = t["gridPos"]
        s = strips[(g["x"], g["y"] + g["h"])]
        assert s["type"] == "timeseries" and s["gridPos"]["w"] == g["w"] and s["transparent"]
        assert [x["expr"] for x in s["targets"]] == [x["expr"] for x in t["targets"]]
        assert s["fieldConfig"]["defaults"]["custom"]["axisPlacement"] == "hidden"
        assert not s["options"]["legend"]["showLegend"]
        assert s["fieldConfig"]["defaults"]["color"] == {"mode": "fixed", "fixedColor": monitor.STRIP_COLOUR}


def test_panel_ids_the_screenshots_open_stay_put():
    by_id = {p["id"]: p["title"] for p in _all_panels(monitor.dashboard())}
    assert "memory" in by_id[31].lower() and "power" in by_id[37].lower()


def _ini_sections(text):
    import configparser

    c = configparser.ConfigParser(interpolation=None)
    c.read_string(text)
    return c


def test_grafana_is_view_only_and_nothing_can_be_sent_off_the_mac():
    c = _ini_sections(monitor.grafana_ini(monitor.STATE, "pw"))
    assert c["snapshots"]["external_enabled"] == "false"  # no upload to snapshots.raintank.io
    assert c["public_dashboards"]["enabled"] == "false"  # no "share externally" links
    assert c["auth"]["disable_login_form"] == "true" and c["auth.basic"]["enabled"] == "false"
    assert c["plugins"]["plugin_admin_enabled"] == "false"  # no plugin catalog from grafana.com
    assert c["plugins"]["preinstall_disabled"] == "true"  # no plugin downloads from grafana.com at start
    assert c["unified_alerting"]["enabled"] == "false"  # no alert engine running with no alerts
    assert c["auth.anonymous"]["enabled"] == "true" and c["auth.anonymous"]["org_role"] == "Viewer"


def test_charts_split_by_model_list_their_lines_in_a_table_beside_the_chart():
    folded = [
        p for row in monitor.dashboard()["panels"] if row["type"] == "row" for p in row.get("panels", [])
    ]
    for p in folded:  # the overview's one wide chart keeps its short legend underneath
        per_model = any("{{model_name}}" in t["legendFormat"] for t in p["targets"])
        legend = p.get("options", {}).get("legend", {})
        if per_model and len(p["targets"]) >= 2:
            assert legend.get("displayMode") == "table" and legend.get("placement") == "right", p["title"]
            assert legend.get("calcs") == ["lastNotNull", "max"], p["title"]
            assert p["gridPos"]["w"] == 24 and p["gridPos"]["x"] == 0, p["title"]  # room for the table


def test_the_gpu_limit_is_one_line_not_one_per_model():
    memory = next(p for p in _all_panels(monitor.dashboard()) if p["title"].startswith("MLX GPU memory"))
    limit = [t for t in memory["targets"] if "gpu_memory_limit" in t["expr"]]
    assert len(limit) == 1 and limit[0]["expr"].startswith("max(") and "{{" not in limit[0]["legendFormat"]


def test_folded_charts_never_overlap():
    for row in monitor.dashboard()["panels"]:
        cells = set()
        for p in row.get("panels", []):
            g = p["gridPos"]
            mine = {(x, y) for x in range(g["x"], g["x"] + g["w"]) for y in range(g["y"], g["y"] + g["h"])}
            assert not (mine & cells), (row["title"], p["title"])
            assert g["x"] + g["w"] <= 24
            cells |= mine


def test_tiles_show_only_what_is_reporting_now_and_strips_keep_the_history():
    panels = monitor.dashboard()["panels"]
    for p in panels:
        if p["type"] == "stat":  # a finished run drops off at once, so a tile rarely holds two models
            assert all(t.get("instant") is True and t.get("range") is False for t in p["targets"]), p["title"]
        if p["type"] == "timeseries":
            assert not any(t.get("instant") for t in p["targets"]), p["title"]
