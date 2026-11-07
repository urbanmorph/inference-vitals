import json
import re

import pytest

from inference_vitals import gpustat, metrics, monitor, sysstat


def _known_metric_names():
    m = metrics.Metrics(total=1, kv_per_token=1, labels={"model_name": "x"})
    m.started({})
    m.progress(tokens=1, writing_seconds=1.0, prompt_tokens=1)
    writing = m.render()  # some numbers exist only while a page is being written
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
    ours = {line.split("{")[0].split(" ")[0] for line in (writing + m.render()).splitlines()}
    return {
        monitor.MAC: ours | set(monitor.MACMON_METRICS) | set(sysstat.METRICS) | set(sysstat.MAC_ONLY),
        monitor.LINUX: ours | set(gpustat.METRICS) | set(sysstat.METRICS) | set(sysstat.LINUX_ONLY),
    }


def _all_panels(dashboard):
    for panel in dashboard["panels"]:
        yield panel
        yield from panel.get("panels", [])


def _exprs(dashboard):
    for panel in _all_panels(dashboard):
        for t in panel.get("targets", []):
            yield panel["title"], t["expr"]


@pytest.mark.parametrize("platform", monitor.PLATFORMS)
def test_every_dashboard_query_uses_a_metric_that_exists_on_that_platform(platform):
    known = _known_metric_names()[platform]
    for title, expr in _exprs(monitor.dashboard(platform)):
        names = set(re.findall(r"\b(?:vllm:|vitals_|macmon_)[A-Za-z0-9_:]+", expr))
        assert names, (title, expr)
        assert names <= known, (platform, title, names - known)


def test_the_linux_dashboard_shows_nvidia_numbers_where_the_mac_one_shows_apple_s():
    tiles = {p["title"]: p for p in monitor.dashboard(monitor.LINUX)["panels"] if p["type"] == "stat"}
    assert "vitals_gpu_memory_free_bytes" in tiles["GPU mem left"]["targets"][0]["expr"]
    assert "vitals_gpu_slowdown_state" in tiles["GPU slowdown"]["targets"][0]["expr"]
    assert "GPU power" in tiles and "Mac power" not in tiles
    names = {
        m["options"]["2"]["text"]
        for m in tiles["GPU slowdown"]["fieldConfig"]["defaults"]["mappings"]
        if m["type"] == "value"
    }
    assert names == {"Thermal"}
    titles = {p["title"] for p in _all_panels(monitor.dashboard(monitor.LINUX))}
    assert (
        "GPU memory (VRAM), used against total" in titles
        and "MLX GPU memory, against the GPU limit" not in titles
    )


def test_dashboard_panels_point_at_our_prometheus_and_are_json():
    d = json.loads(json.dumps(monitor.dashboard()))
    assert d["uid"] == "inference-vitals" and d["refresh"] == "10s"
    for p in _all_panels(d):
        if p["type"] != "row":
            assert p["datasource"]["uid"] == monitor.DATASOURCE_UID


TOOLS = {
    "prometheus": "/usr/bin/prometheus",
    "grafana": "/usr/sbin/grafana",
    "grafana_home": "/usr/share/grafana",
    "macmon": "/opt/homebrew/bin/macmon",
    "nvidia": True,
}


def test_everything_listens_on_localhost_only():
    prom = monitor.prometheus_config()
    assert "127.0.0.1:8400" in prom and "127.0.0.1:9091" in prom
    ini = monitor.grafana_ini(monitor.STATE, "pw")
    assert "http_addr = 127.0.0.1" in ini
    assert "reporting_enabled = false" in ini and "check_for_updates = false" in ini
    cmds = monitor.commands(monitor.STATE, monitor.MAC, TOOLS)
    assert cmds["macmon"][-4:] == ["--host", "127.0.0.1", "--port", "9091"]
    assert "--web.listen-address=127.0.0.1:9090" in cmds["prometheus"]
    assert cmds["sysstat"][-2:] == ["--port", "9092"] and "127.0.0.1:9092" in prom


def test_on_linux_the_nvidia_exporter_takes_macmon_s_place():
    cmds = monitor.commands(monitor.STATE, monitor.LINUX, TOOLS, ui=monitor.GRAFANA_UI)
    assert "macmon" not in cmds and cmds["gpustat"][-2:] == ["--port", "9091"]
    assert cmds["prometheus"][0] == "/usr/bin/prometheus"
    assert (
        cmds["grafana"][:2] == ["/usr/sbin/grafana", "server"]
        and "--homepath=/usr/share/grafana" in cmds["grafana"]
    )
    assert "gpustat" not in monitor.commands(monitor.STATE, monitor.LINUX, {**TOOLS, "nvidia": False})


def test_tools_are_found_on_the_path_and_grafana_s_home_beside_it(tmp_path):
    (tmp_path / "bin").mkdir()
    for name in ("prometheus", "grafana-server"):
        (tmp_path / "bin" / name).write_text("")
        (tmp_path / "bin" / name).chmod(0o755)
    (tmp_path / "share/grafana/conf").mkdir(parents=True)
    tools = monitor.find_tools(monitor.LINUX, path=str(tmp_path / "bin"), homes=[tmp_path / "nowhere"])
    assert tools["prometheus"] == str(tmp_path / "bin/prometheus")
    assert tools["grafana"] == str(
        tmp_path / "bin/grafana-server"
    )  # the older name, when that is all there is
    assert tools["grafana_home"] == str(tmp_path / "share/grafana")


def test_an_older_grafana_server_binary_takes_no_server_word():
    cmds = monitor.commands(
        monitor.STATE, monitor.LINUX, {**TOOLS, "grafana": "/usr/sbin/grafana-server"}, ui=monitor.GRAFANA_UI
    )
    assert cmds["grafana"][1].startswith("--homepath=")


def test_the_platform_is_detected():
    assert monitor.detect_platform("darwin") == monitor.MAC
    assert monitor.detect_platform("linux") == monitor.LINUX
    with pytest.raises(SystemExit):
        monitor.detect_platform("win32")


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
    assert tile["targets"][0]["expr"] == "max(vitals_host_thermal_state)"
    names = {
        m["options"][str(v)]["text"]
        for m in tile["fieldConfig"]["defaults"]["mappings"]
        if m["type"] == "value"
        for v in range(4)
    }
    assert names == {"Nominal", "Fair", "Serious", "Critical"}


def test_tiles_show_plain_numbers_with_no_graph_behind_them_and_name_each_model():
    tiles = [p for p in monitor.dashboard()["panels"] if p.get("type") == "stat" and p["title"] != "Running"]
    assert len(tiles) == 16
    for t in tiles:
        assert t["options"]["graphMode"] == "none", t["title"]
        assert t["options"]["textMode"] == "auto", t["title"]  # a name above each number when two models show


def test_every_tile_has_a_small_trend_strip_right_under_it():
    panels = monitor.dashboard()["panels"]
    tiles = [p for p in panels if p["type"] == "stat" and p["title"] != "Running"]
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


def test_missing_tools_are_named_with_how_to_install_them():
    assert monitor.missing_tools(monitor.MAC, TOOLS) == []
    gone = monitor.missing_tools(monitor.MAC, {**TOOLS, "macmon": None, "prometheus": None})
    assert any("macmon" in g and "brew install" in g for g in gone) and any("prometheus" in g for g in gone)
    linux = monitor.missing_tools(
        monitor.LINUX, {**TOOLS, "grafana": None, "grafana_home": None}, monitor.GRAFANA_UI
    )
    assert any("grafana" in g and "README" in g for g in linux)
    assert monitor.missing_tools(monitor.LINUX, {**TOOLS, "macmon": None}) == []  # not needed on Linux


@pytest.mark.parametrize("platform", monitor.PLATFORMS)
def test_machine_tiles_always_show_one_number(platform):
    """A tile about the machine (not a model) is one figure, even when Prometheus briefly holds two copies
    of a series, e.g. after a job is renamed; so its query is wrapped in max(), min() or sum()."""
    machine = [
        p
        for p in monitor.dashboard(platform)["panels"]
        if p["type"] == "stat" and p["targets"][0]["legendFormat"] == ""
    ]
    assert len(machine) == 8
    for p in machine:
        expr = p["targets"][0]["expr"]
        assert re.match(r"^(max|min|sum)\(", expr), (platform, p["title"], expr)


def test_context_used_is_live_while_a_page_is_written_and_holds_between_pages():
    tile = next(p for p in monitor.dashboard()["panels"] if p.get("title") == "Context used")
    expr = tile["targets"][0]["expr"]
    assert "vitals_current_context_tokens or vitals_last_context_used_tokens" in expr


def test_a_band_above_the_tiles_names_each_running_model_and_its_phase():
    panels = monitor.dashboard()["panels"]
    band = next(p for p in panels if p.get("title") == "Running")
    assert band["type"] == "stat" and band["gridPos"]["w"] == 24 and band["gridPos"]["y"] == 1
    assert band["targets"][0]["expr"] == "vitals_current_phase"
    assert band["targets"][0]["legendFormat"] == "{{model_name}}"
    assert band["options"]["textMode"] == "value_and_name"  # the model's name beside its phase
    assert band["options"]["text"]["valueSize"] >= 16  # Grafana shrinks text in a short panel otherwise
    names = band["fieldConfig"]["defaults"]["mappings"][-1]["options"]
    assert [names[k]["text"] for k in ("0", "1", "2")] == ["Idle", "Reading (prefill)", "Writing (decode)"]
    tiles = [p for p in panels if p["type"] == "stat" and p is not band]
    assert min(p["gridPos"]["y"] for p in tiles) >= 1 + band["gridPos"]["h"]  # tiles start below it
    strips = [p for p in panels if p.get("title") == "" and p["gridPos"]["w"] == 24]
    assert not strips  # no trend strip under the band


def test_the_dashboard_links_to_the_repository():
    links = monitor.dashboard()["links"]
    assert links == [
        {
            "title": "inference-vitals on GitHub",
            "type": "link",
            "url": "https://github.com/urbanmorph/inference-vitals",
            "icon": "external link",
            "targetBlank": True,
            "tooltip": "Source, setup and what each number means",
            "asDropdown": False,
            "includeVars": False,
            "keepTime": False,
            "tags": [],
        }
    ]


def test_the_page_is_the_default_and_grafana_is_optional():
    page = monitor.commands(monitor.STATE, monitor.MAC, TOOLS)
    assert "grafana" not in page
    assert page["page"][1:] == ["-m", "inference_vitals.ui", "--port", "3000", "--platform", "mac"]
    grafana = monitor.commands(monitor.STATE, monitor.MAC, TOOLS, ui=monitor.GRAFANA_UI)
    assert "page" not in grafana and grafana["grafana"][0] == TOOLS["grafana"]


def test_grafana_is_only_needed_when_asked_for():
    no_grafana = {**TOOLS, "grafana": None, "grafana_home": None}
    assert monitor.missing_tools(monitor.MAC, no_grafana) == []
    assert any("grafana" in m for m in monitor.missing_tools(monitor.MAC, no_grafana, monitor.GRAFANA_UI))


def test_start_takes_ui_grafana_and_nothing_else():
    assert monitor.chosen_ui([]) == monitor.PAGE_UI
    assert monitor.chosen_ui(["--ui", "grafana"]) == monitor.GRAFANA_UI
    with pytest.raises(SystemExit):
        monitor.chosen_ui(["--ui", "kibana"])


def test_stop_and_status_cover_whichever_dashboard_runs():
    assert {"page", "grafana"} <= set(monitor._names(monitor.MAC))


def test_the_page_will_not_start_where_grafana_already_holds_the_port(tmp_path):
    import os

    (tmp_path / "pids").mkdir()
    (tmp_path / "pids" / "grafana").write_text(str(os.getpid()))  # a process that is certainly running
    assert "inference-vitals stop" in monitor.ui_conflict(tmp_path, monitor.PAGE_UI)
    assert monitor.ui_conflict(tmp_path, monitor.GRAFANA_UI) is None


def test_the_address_printed_matches_the_dashboard():
    assert monitor.dashboard_url(monitor.PAGE_UI) == "http://127.0.0.1:3000/"
    assert monitor.dashboard_url(monitor.GRAFANA_UI).endswith("/d/inference-vitals")


@pytest.mark.parametrize("platform", monitor.PLATFORMS)
def test_energy_per_token_is_the_model_s_share_of_power_over_tokens_everywhere(platform):
    q = monitor.HW[platform]["joules"]
    meter = monitor.HW[platform]["meter"]
    assert q.startswith(
        f"sum(avg_over_time((clamp_min({meter} - scalar(quantile_over_time(0.1, ({meter})[24h:1m]))"
    )
    assert monitor.ACTIVE in q and "rate(vllm:generation_tokens_total[2m])" in q
    titles = {p["title"]: p for row in monitor.dashboard(platform)["panels"] for p in row.get("panels", [])}
    chart = titles["Energy per answer token (the model's share of power ÷ tokens, over 2 min)"]
    assert chart["targets"][0]["expr"] == q
    tile = next(p for p in monitor.dashboard(platform)["panels"] if p.get("title") == "Energy/token")
    assert tile["targets"][0]["expr"] == q


def test_the_whole_machine_is_metered_on_a_mac_and_the_gpus_on_linux():
    assert monitor.HW[monitor.MAC]["meter"] == "max(macmon_sys_power_watts)"
    assert monitor.HW[monitor.LINUX]["meter"] == "sum(vitals_gpu_power_watts)"
