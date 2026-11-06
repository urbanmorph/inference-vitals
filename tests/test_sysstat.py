from inference_vitals import sysstat

VM = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                   709779.
Pageins:                                    29414384.
Pageouts:                                      38007.
Swapins:                                     6912675.
Swapouts:                                   10618742.
"""


def test_vm_stat_counters_in_bytes():
    v = sysstat.parse_vm_stat(VM)
    assert v["swapped_in_bytes_total"] == 6912675 * 16384
    assert v["swapped_out_bytes_total"] == 10618742 * 16384
    assert v["paged_in_bytes_total"] == 29414384 * 16384 and v["paged_out_bytes_total"] == 38007 * 16384


def test_memory_pressure_and_swap_usage():
    assert sysstat.memory_free_percent("77\n") == 77
    assert sysstat.swap_usage("total = 3072.00M  used = 1866.19M  free = 1205.81M  (encrypted)") == (
        int(3072.00 * 2**20),
        int(1866.19 * 2**20),
    )


def _mac(thermal):
    return sysstat.MacHost(
        read=lambda cmd: {
            "vm_stat": VM,
            "kern.memorystatus_level": "77",
            "vm.swapusage": "total = 1024.00M  used = 512.00M  free = 512.00M",
        }[cmd],
        thermal=lambda: thermal,
    )


def test_mac_render_has_every_number_with_fake_readers():
    text = _mac(2).render()
    for name in sysstat.METRICS + sysstat.MAC_ONLY:
        assert name in text, name
    assert "vitals_host_memory_free_percent 77" in text
    assert "vitals_host_thermal_state 2" in text


def test_thermal_state_is_read_live_from_macos():
    assert sysstat.thermal_state() in (0, 1, 2, 3)


def test_a_thermal_state_that_cannot_be_read_is_left_out():
    assert "thermal_state" not in _mac(None).render()


# Linux: the same numbers from /proc, and the CPU's own temperature and busy share.

MEMINFO = """MemTotal:       32768000 kB
MemFree:         1000000 kB
MemAvailable:    8192000 kB
SwapTotal:       4194304 kB
SwapFree:        3145728 kB
"""
VMSTAT = "nr_free_pages 250000\npgpgin 1000\npgpgout 2000\npswpin 30\npswpout 40\n"
STAT_1 = "cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 50 0 25 400 25 0 0 0 0 0\n"
STAT_2 = "cpu  200 0 100 850 50 0 0 0 0 0\ncpu0 100 0 50 425 25 0 0 0 0 0\n"


def _hwmon(root):
    for n, (name, temps) in enumerate([("k10temp", [65000, 71000]), ("nvme", [40000])]):
        d = root / f"hwmon{n}"
        d.mkdir(parents=True)
        (d / "name").write_text(name + "\n")
        for i, t in enumerate(temps, 1):
            (d / f"temp{i}_input").write_text(f"{t}\n")


def _linux(tmp_path, stats):
    _hwmon(tmp_path / "hwmon")
    files = {"/proc/meminfo": MEMINFO, "/proc/vmstat": VMSTAT}
    stats = iter(stats)

    def read(path):
        return next(stats) if path == "/proc/stat" else files[path]

    return sysstat.LinuxHost(read=read, hwmon=tmp_path / "hwmon", page_size=4096)


def test_linux_memory_swap_and_paging_from_proc(tmp_path):
    v = _linux(tmp_path, [STAT_1]).values()
    assert v["vitals_host_memory_free_percent"] == 25  # MemAvailable / MemTotal
    assert v["vitals_host_swap_total_bytes"] == 4194304 * 1024
    assert v["vitals_host_swap_used_bytes"] == (4194304 - 3145728) * 1024
    assert v["vitals_host_swapped_in_bytes_total"] == 30 * 4096  # pswpin counts pages
    assert v["vitals_host_swapped_out_bytes_total"] == 40 * 4096
    assert v["vitals_host_paged_in_bytes_total"] == 1000 * 1024  # pgpgin counts KiB
    assert v["vitals_host_paged_out_bytes_total"] == 2000 * 1024


def test_linux_cpu_busy_needs_two_readings_and_temperature_comes_from_the_cpu_sensor(tmp_path):
    host = _linux(tmp_path, [STAT_1, STAT_2])
    first = host.values()
    assert "vitals_host_cpu_busy_ratio" not in first  # no interval yet
    assert first["vitals_host_cpu_temp_celsius"] == 71.0  # hottest CPU sensor; the NVMe one is ignored
    assert host.values()["vitals_host_cpu_busy_ratio"] == 0.75  # 150 busy of 200 ticks


def test_linux_render_has_every_number(tmp_path):
    host = _linux(tmp_path, [STAT_1, STAT_2])
    host.values()
    text = host.render()
    for name in sysstat.METRICS + sysstat.LINUX_ONLY:
        assert name in text, name


def test_no_cpu_sensor_means_no_temperature(tmp_path):
    host = sysstat.LinuxHost(
        read=lambda p: {"/proc/meminfo": MEMINFO, "/proc/vmstat": VMSTAT, "/proc/stat": STAT_1}[p],
        hwmon=tmp_path / "none",
        page_size=4096,
    )
    assert "vitals_host_cpu_temp_celsius" not in host.values()
