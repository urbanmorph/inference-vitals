from inference_vitals import conditions

COOL = """Note: No thermal warning level has been recorded
Note: No performance warning level has been recorded
Note: No CPU power status has been recorded
"""
HOT = """Thermal warning level set to 2.
Performance warning level set to 1.
CPU_Speed_Limit = 72
"""


def test_thermal_reads_both_cool_and_throttled():
    assert conditions.thermal(COOL) == {
        "thermal_warning": 0,
        "performance_warning": 0,
        "cpu_speed_limit": 100,
    }
    assert conditions.thermal(HOT) == {"thermal_warning": 2, "performance_warning": 1, "cpu_speed_limit": 72}


def test_power_source_and_mode():
    assert conditions.on_mains("Now drawing from 'AC Power'\n -InternalBattery-0 91%; charging")
    assert not conditions.on_mains("Now drawing from 'Battery Power'\n -InternalBattery-0 64%; discharging")
    assert conditions.power_mode(" powermode            1\n") == "low power"
    assert conditions.power_mode(" powermode            0\n") == "normal"
    assert conditions.power_mode("no such line") == "unknown"


def test_busy_processes_keep_names_only_and_skip_idle_ones():
    ps = "  95.0 /Applications/Foo.app/Contents/MacOS/Foo\n  33.0 claude\n   2.0 /usr/sbin/idle\n"
    assert conditions.busy_processes(ps, over=20) == [["Foo", 95.0], ["claude", 33.0]]


def test_snapshot_has_the_fields_a_run_needs():
    snap = conditions.snapshot()
    for key in (
        "at",
        "macos",
        "model",
        "on_mains",
        "power_mode",
        "thermal_warning",
        "gpu_wired_limit_mb",
        "busy_processes",
    ):
        assert key in snap


def test_a_missing_command_gives_an_empty_answer_not_a_crash():
    assert conditions._out("no-such-command-anywhere") == ""
