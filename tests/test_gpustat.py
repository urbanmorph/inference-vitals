from inference_vitals import gpustat

# nvidia-smi --query-gpu=<gpustat.FIELDS> --format=csv,noheader,nounits, on a two-GPU machine
GPUS = (
    "0, NVIDIA GeForce RTX 4090, 97, 24564, 20480, 3836, 74, 410.25, 450.00, 2520, 10501, 65\n"
    "1, NVIDIA RTX A2000, 0, 6138, 1, 5900, 38, [N/A], [N/A], 210, 405, [N/A]\n"
)
REASONS = "0, Active, Not Active, Not Active, Not Active, Not Active\n1, Not Active, Not Active, Active, Not Active, Not Active\n"
OLD_DRIVER = 'Field "clocks_event_reasons.sw_power_cap" is not a valid field to query.\n'


def _smi(reasons_ok=True, old_names_ok=True):
    calls = []

    def run(args):
        calls.append(args)
        query = next(a for a in args if a.startswith("--query-gpu="))
        if "clocks_event_reasons" in query:
            return (0, REASONS, "") if reasons_ok else (2, OLD_DRIVER, "")
        if "clocks_throttle_reasons" in query:
            return (0, REASONS, "") if old_names_ok else (2, "not valid", "")
        return 0, GPUS, ""

    return run, calls


def test_each_gpu_is_reported_with_its_own_labels_and_units():
    run, _ = _smi()
    text = gpustat.NvidiaGpus(run=run).render()
    g0 = '{gpu="0",name="NVIDIA GeForce RTX 4090"}'
    assert f"vitals_gpu_busy_ratio{g0} 0.97" in text
    assert f"vitals_gpu_memory_total_bytes{g0} {24564 * 2**20}" in text
    assert f"vitals_gpu_memory_free_bytes{g0} {3836 * 2**20}" in text
    assert f"vitals_gpu_temp_celsius{g0} 74.0" in text
    assert f"vitals_gpu_power_watts{g0} 410.25" in text
    assert f"vitals_gpu_freq_mhz{g0} 2520.0" in text
    assert f"vitals_gpu_fan_ratio{g0} 0.65" in text
    assert f"vitals_gpu_slowdown_state{g0} 1" in text  # at its power limit: normal under load


def test_values_a_gpu_does_not_report_are_left_out_not_zero():
    run, _ = _smi()
    text = gpustat.NvidiaGpus(run=run).render()
    g1 = '{gpu="1",name="NVIDIA RTX A2000"}'
    assert f"vitals_gpu_temp_celsius{g1} 38.0" in text
    assert f"vitals_gpu_power_watts{g1}" not in text and f"vitals_gpu_fan_ratio{g1}" not in text
    assert f"vitals_gpu_slowdown_state{g1} 2" in text  # hardware thermal slowdown


def test_an_older_driver_s_names_for_the_slowdown_reasons_are_tried_and_remembered():
    run, calls = _smi(reasons_ok=False)
    gpus = gpustat.NvidiaGpus(run=run)
    assert "vitals_gpu_slowdown_state" in gpus.render()
    gpus.render()
    queries = [next(a for a in c if a.startswith("--query-gpu=")) for c in calls]
    assert sum("clocks_event_reasons" in q for q in queries) == 1  # tried once, then not again


def test_no_slowdown_reasons_at_all_still_reports_the_rest():
    run, _ = _smi(reasons_ok=False, old_names_ok=False)
    text = gpustat.NvidiaGpus(run=run).render()
    assert "vitals_gpu_busy_ratio" in text and "slowdown_state" not in text


def test_a_failing_nvidia_smi_reports_nothing_rather_than_crashing():
    text = gpustat.NvidiaGpus(run=lambda args: (9, "", "NVIDIA-SMI has failed")).render()
    assert text == ""


def test_slowdown_state_names_the_worst_reason():
    s = gpustat.slowdown_state
    assert s({}) == 0
    assert s({"sw_power_cap": True}) == 1
    assert s({"sw_power_cap": True, "sw_thermal_slowdown": True}) == 2
    assert s({"hw_slowdown": True}) == 3 and s({"hw_power_brake_slowdown": True}) == 3


def test_a_quote_in_a_gpu_name_cannot_break_the_numbers_page():
    assert gpustat._label('A "B" \\C') == 'A \\"B\\" \\\\C'


def test_every_metric_it_can_publish_is_listed():
    run, _ = _smi()
    published = {line.split("{")[0] for line in gpustat.NvidiaGpus(run=run).render().splitlines()}
    assert published == set(gpustat.METRICS)
