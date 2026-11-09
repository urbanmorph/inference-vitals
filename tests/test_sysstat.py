import sys

import pytest

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
            "sysctl": "77\ntotal = 1024.00M  used = 512.00M  free = 512.00M\n",  # one call, both values
            "ioreg": "",  # no battery
        }[cmd],
        thermal=lambda: thermal,
        gpu_limit=lambda: 40200896512,  # not Metal's: the tests run on Linux too
    )


def test_mac_render_has_every_number_with_fake_readers():
    text = _mac(2).render()
    for name in sysstat.METRICS + sysstat.MAC_ONLY:
        assert name in text, name
    assert "vitals_host_memory_free_percent 77" in text
    assert "vitals_host_thermal_state 2" in text


@pytest.mark.skipif(sys.platform != "darwin", reason="reads macOS itself")
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

    return sysstat.LinuxHost(
        read=read, hwmon=tmp_path / "hwmon", page_size=4096, power_supply=tmp_path / "none"
    )


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


# The battery: only where there is one. Made-up readings in the shapes macOS and Linux give them.

IOREG = """+-o AppleSmartBattery  <class AppleSmartBattery, id 0x100000271, registered, matched, active, busy 0>
    {
      "TimeRemaining" = 87
      "ExternalConnected" = No
      "Voltage" = 12400
      "MaxCapacity" = 100
      "CurrentCapacity" = 64
      "BatteryData" = {"CurrentCapacity"=64,"AppleRawMaxCapacity"=6365,"RemainingCapacity"=4000,"DesignCapacity"=6249}
      "IsCharging" = No
      "InstantAmperage" = -2000
    }
"""


def test_a_macs_battery_gives_its_charge_the_energy_left_and_whether_it_is_on_power():
    b = sysstat.mac_battery(IOREG)
    assert b == {
        "vitals_battery_percent": 64,
        "vitals_battery_remaining_wh": 49.6,
        "vitals_battery_on_power": 0,
        "vitals_battery_draw_watts": 24.8,  # 2 A out at 12.4 V: what the battery gives right now
    }
    assert (
        sysstat.mac_battery(IOREG.replace('"ExternalConnected" = No', '"ExternalConnected" = Yes'))[
            "vitals_battery_on_power"
        ]
        == 1
    )
    assert sysstat.mac_battery("") is None  # a desktop Mac has none


def test_the_battery_is_in_the_macs_numbers_only_when_there_is_one():
    with_battery = sysstat.MacHost(
        read=lambda cmd: {"ioreg": IOREG}.get(cmd) or _mac(0).read(cmd),
        thermal=lambda: 0,
        gpu_limit=lambda: None,
    )
    assert "vitals_battery_remaining_wh 49.6" in with_battery.render()
    assert "vitals_battery" not in _mac(0).render()


def _power_supply(tmp_path, **files):
    bat = tmp_path / "power_supply" / "BAT0"
    bat.mkdir(parents=True)
    for name, text in files.items():
        (bat / name).write_text(text)
    return tmp_path / "power_supply"


def test_a_linux_laptops_battery_from_sys(tmp_path):
    root = _power_supply(tmp_path, capacity="80\n", energy_now="40000000\n", status="Discharging\n",
                         power_now="15000000\n")  # fmt: skip
    assert sysstat.linux_battery(root) == {"vitals_battery_percent": 80, "vitals_battery_remaining_wh": 40.0,
                                           "vitals_battery_on_power": 0, "vitals_battery_draw_watts": 15.0}  # fmt: skip


def test_a_linux_battery_that_reports_charge_not_energy(tmp_path):
    root = _power_supply(
        tmp_path, capacity="50\n", charge_now="3000000\n", voltage_now="12000000\n", status="Charging\n"
    )
    b = sysstat.linux_battery(root)
    assert b["vitals_battery_remaining_wh"] == 36.0 and b["vitals_battery_on_power"] == 1


def test_a_server_with_no_battery_has_none(tmp_path):
    assert sysstat.linux_battery(tmp_path / "power_supply") is None


def test_on_power_nothing_is_drawn_from_the_battery():
    plugged = sysstat.mac_battery(IOREG.replace('"ExternalConnected" = No', '"ExternalConnected" = Yes'))
    assert "vitals_battery_draw_watts" not in plugged


def test_a_negative_current_printed_as_an_unsigned_number_is_read_as_negative():
    unsigned = str(2**64 - 2000)  # how ioreg often prints -2000 mA
    b = sysstat.mac_battery(IOREG.replace('"InstantAmperage" = -2000', f'"InstantAmperage" = {unsigned}'))
    assert b["vitals_battery_draw_watts"] == 24.8


def test_a_mac_read_is_three_quick_processes_and_the_battery_is_fresh_every_time():
    # plugging in should show within a scrape: ioreg takes ~15 ms, so it is read every time
    reads = []
    base = _mac(0).read
    host = sysstat.MacHost(
        read=lambda cmd: reads.append(cmd) or base(cmd), thermal=lambda: 0, gpu_limit=lambda: None
    )
    host.render()
    host.render()
    assert reads == ["vm_stat", "sysctl", "ioreg"] * 2


@pytest.mark.skipif(sys.platform != "darwin", reason="reads macOS itself")
def test_the_thermal_state_reader_loads_macos_libraries_once():
    sysstat.thermal_state()
    sysstat.thermal_state()
    assert sysstat._objc.cache_info().misses == 1


def test_a_mac_publishes_its_gpu_memory_limit():
    host = sysstat.MacHost(read=_mac(0).read, thermal=lambda: 0, gpu_limit=lambda: 40200896512)
    assert "vitals_host_gpu_memory_limit_bytes 40200896512" in host.render()
    none = sysstat.MacHost(read=_mac(0).read, thermal=lambda: 0, gpu_limit=lambda: None)
    assert "gpu_memory_limit" not in none.render()  # unreadable: left out, not a zero


@pytest.mark.skipif(sys.platform != "darwin", reason="reads macOS itself")
def test_the_gpu_memory_limit_is_read_from_metal_as_mlx_reads_it():
    limit = sysstat.gpu_memory_limit()
    assert limit and 1e9 < limit < 1e12
    assert (
        sysstat.gpu_memory_limit.cache_info().misses == 1 or sysstat.gpu_memory_limit() == limit
    )  # read once
