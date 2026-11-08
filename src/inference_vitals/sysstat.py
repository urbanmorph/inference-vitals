"""The machine's memory (and on Linux its CPU) as Prometheus numbers, all the time, on 127.0.0.1:9092
(started by inference-vitals).

What shows a model being squeezed is swap *activity* (bytes moved in and out of swap, as counters
Prometheus turns into a rate) and the system's own measure of free memory; swap merely in use says
little, since both macOS and Linux keep old swap around for hours.

- macOS: vm_stat and sysctl (memory pressure as `memory_pressure` reports it), plus macOS's thermal
  state (0 nominal, 1 fair, 2 serious, 3 critical), which rises with heat well before the throttling
  warning `pmset -g therm` reports. CPU numbers come from macmon.
- Linux: /proc/meminfo (MemAvailable), /proc/vmstat (swap and paging), /proc/stat (CPU busy share) and
  the CPU's own temperature sensor under /sys/class/hwmon.

On a laptop, also its battery: charge, the energy left (watt-hours) and whether it is on power, from
macOS's AppleSmartBattery (ioreg) or Linux's /sys/class/power_supply. Nothing where there is none.

No sudo, read each time Prometheus asks.

  python -m inference_vitals.sysstat --port 9092
"""

import argparse
import ctypes
import ctypes.util
import os
import pathlib
import re
import subprocess
import sys
import time

METRICS = [  # on every platform
    "vitals_host_swapped_in_bytes_total",
    "vitals_host_swapped_out_bytes_total",
    "vitals_host_paged_in_bytes_total",
    "vitals_host_paged_out_bytes_total",
    "vitals_host_memory_free_percent",
    "vitals_host_swap_total_bytes",
    "vitals_host_swap_used_bytes",
]
MAC_ONLY = ["vitals_host_thermal_state"]
BATTERY = [  # laptops only; the draw only while it runs on the battery
    "vitals_battery_percent",
    "vitals_battery_remaining_wh",
    "vitals_battery_on_power",
    "vitals_battery_draw_watts",
]
LINUX_ONLY = ["vitals_host_cpu_busy_ratio", "vitals_host_cpu_temp_celsius"]
VM_FIELDS = {
    "Swapins": "swapped_in",
    "Swapouts": "swapped_out",
    "Pageins": "paged_in",
    "Pageouts": "paged_out",
}
CPU_SENSORS = {"coretemp", "k10temp", "zenpower", "cpu_thermal"}  # Intel, AMD, AMD (alt driver), ARM boards


def parse_vm_stat(text: str) -> dict:
    page = int(re.search(r"page size of (\d+) bytes", text).group(1))
    out = {}
    for field, name in VM_FIELDS.items():
        m = re.search(rf"^{field}:\s+(\d+)\.", text, re.MULTILINE)
        if m:
            out[f"{name}_bytes_total"] = int(m.group(1)) * page
    return out


def memory_free_percent(text: str):
    text = text.strip()
    return int(text) if text.isdigit() else None


def swap_usage(text: str):
    sizes = dict(re.findall(r"(total|used) = ([\d.]+)M", text))
    return int(float(sizes["total"]) * 2**20), int(float(sizes["used"]) * 2**20)


def thermal_state():
    """NSProcessInfo.thermalState, 0-3, through the Objective-C runtime (no compiler, no packages);
    None if it cannot be read."""
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))
        for f in (objc.objc_getClass, objc.sel_registerName):
            f.restype, f.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
        send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        send_long = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        info = send(objc.objc_getClass(b"NSProcessInfo"), objc.sel_registerName(b"processInfo"))
        state = send_long(info, objc.sel_registerName(b"thermalState"))
    except (OSError, TypeError, AttributeError):
        return None
    return state if state in (0, 1, 2, 3) else None


def mac_battery(text: str):
    """From `ioreg -rn AppleSmartBattery`: charge (%), energy left (mAh left x volts) and whether it is on
    power; None with no battery."""

    def num(pattern):
        m = re.search(pattern, text)
        return int(m.group(1)) if m else None

    percent, volts_mv = num(r'"CurrentCapacity" = (\d+)'), num(r'"Voltage" = (\d+)')
    mah = num(r'"(?:AppleRawCurrentCapacity|RemainingCapacity)"\s*=\s*(\d+)')
    if percent is None or volts_mv is None or mah is None:
        return None
    out = {
        "vitals_battery_percent": percent,
        "vitals_battery_remaining_wh": round(mah * volts_mv / 1e6, 1),
        "vitals_battery_on_power": int('"ExternalConnected" = Yes' in text),
    }
    amps = re.search(r'"(?:InstantAmperage|Amperage)" = (-?\d+)', text)  # mA, negative while it gives power
    if not out["vitals_battery_on_power"] and amps:
        ma = int(amps.group(1))
        ma = (
            ma - 2**64 if ma >= 2**63 else ma
        )  # ioreg often prints a negative current as an unsigned 64-bit number
        out["vitals_battery_draw_watts"] = round(abs(ma) * volts_mv / 1e6, 1)
    return out


def linux_battery(root=pathlib.Path("/sys/class/power_supply")):
    """The first battery under /sys/class/power_supply: energy_now in µWh, or charge_now (µAh) x
    voltage_now (µV); on power unless it says it is discharging. None with no battery."""
    for bat in sorted(root.glob("BAT*")) if root.exists() else []:

        def read(name, bat=bat):
            f = bat / name
            return f.read_text().strip() if f.exists() else None

        if read("energy_now"):
            wh = int(read("energy_now")) / 1e6
        elif read("charge_now") and read("voltage_now"):
            wh = int(read("charge_now")) * int(read("voltage_now")) / 1e12
        else:
            continue
        out = {
            "vitals_battery_percent": int(read("capacity") or 0),
            "vitals_battery_remaining_wh": round(wh, 1),
            "vitals_battery_on_power": int(read("status") != "Discharging"),
        }
        if not out["vitals_battery_on_power"]:  # µW, or µA x µV
            if read("power_now"):
                out["vitals_battery_draw_watts"] = round(int(read("power_now")) / 1e6, 1)
            elif read("current_now") and read("voltage_now"):
                out["vitals_battery_draw_watts"] = round(
                    int(read("current_now")) * int(read("voltage_now")) / 1e12, 1
                )
        return out
    return None


def _mac_read(name: str) -> str:
    cmd = {"vm_stat": ["vm_stat"], "ioreg": ["ioreg", "-rn", "AppleSmartBattery"]}.get(
        name, ["sysctl", "-n", name]
    )
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


class _Exporter:
    def render(self) -> str:
        return "".join(f"{k} {v}\n" for k, v in self.values().items())

    def snapshot(self) -> str:
        import json

        return json.dumps(self.values())


class MacHost(_Exporter):
    def __init__(self, read=_mac_read, thermal=thermal_state):
        self.read, self.thermal = read, thermal

    def values(self) -> dict:
        v = {f"vitals_host_{k}": n for k, n in parse_vm_stat(self.read("vm_stat")).items()}
        free = memory_free_percent(self.read("kern.memorystatus_level"))
        if free is not None:
            v["vitals_host_memory_free_percent"] = free
        total, used = swap_usage(self.read("vm.swapusage"))
        v["vitals_host_swap_total_bytes"], v["vitals_host_swap_used_bytes"] = total, used
        state = self.thermal()
        if state is not None:
            v["vitals_host_thermal_state"] = state
        return {**v, **(mac_battery(self.read("ioreg")) or {})}


def _fields(text: str) -> dict:
    """'Name:  123 kB' or 'name 123' lines as {name: int}."""
    out = {}
    for line in text.splitlines():
        parts = line.replace(":", " ").split()
        if len(parts) >= 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def _cpu_ticks(stat: str) -> tuple[int, int]:
    """(busy, total) clock ticks from /proc/stat's first line: idle and iowait count as not busy."""
    ticks = [int(x) for x in stat.splitlines()[0].split()[1:9]]
    idle = ticks[3] + ticks[4]
    return sum(ticks) - idle, sum(ticks)


def cpu_temperature(hwmon: pathlib.Path):
    """The hottest reading of the CPU's own sensors (not disks or GPUs), in °C, or None."""
    temps = []
    for d in sorted(hwmon.glob("hwmon*")):
        name = d / "name"
        if name.exists() and name.read_text().strip() in CPU_SENSORS:
            temps += [int(t.read_text()) / 1000 for t in d.glob("temp*_input")]
    return max(temps) if temps else None


def _linux_read(path: str) -> str:
    return pathlib.Path(path).read_text()


class LinuxHost(_Exporter):
    def __init__(self, read=_linux_read, hwmon=pathlib.Path("/sys/class/hwmon"), page_size=None,
                 power_supply=pathlib.Path("/sys/class/power_supply")):  # fmt: skip
        self.read, self.hwmon, self.power_supply = read, hwmon, power_supply
        self.page = page_size or os.sysconf("SC_PAGE_SIZE")
        self.last_ticks = None

    def values(self) -> dict:
        mem, vm = _fields(self.read("/proc/meminfo")), _fields(self.read("/proc/vmstat"))
        v = {
            "vitals_host_swapped_in_bytes_total": vm.get("pswpin", 0) * self.page,
            "vitals_host_swapped_out_bytes_total": vm.get("pswpout", 0) * self.page,
            "vitals_host_paged_in_bytes_total": vm.get("pgpgin", 0) * 1024,  # pgpgin counts KiB, not pages
            "vitals_host_paged_out_bytes_total": vm.get("pgpgout", 0) * 1024,
            "vitals_host_swap_total_bytes": mem.get("SwapTotal", 0) * 1024,
            "vitals_host_swap_used_bytes": (mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) * 1024,
        }
        if mem.get("MemTotal"):
            v["vitals_host_memory_free_percent"] = round(100 * mem.get("MemAvailable", 0) / mem["MemTotal"])
        busy, total = _cpu_ticks(self.read("/proc/stat"))
        if self.last_ticks and total > self.last_ticks[1]:
            v["vitals_host_cpu_busy_ratio"] = round(
                (busy - self.last_ticks[0]) / (total - self.last_ticks[1]), 4
            )
        self.last_ticks = (busy, total)
        temp = cpu_temperature(self.hwmon)
        if temp is not None:
            v["vitals_host_cpu_temp_celsius"] = temp
        return {**v, **(linux_battery(self.power_supply) or {})}


def host():
    return MacHost() if sys.platform == "darwin" else LinuxHost()


def main():
    from inference_vitals.metrics import serve

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9092)
    server = serve(host(), p.parse_args().port)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
