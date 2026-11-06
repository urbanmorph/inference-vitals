"""The Mac's memory as Prometheus numbers, all the time, on 127.0.0.1:9092 (started by inference-vitals).

macmon reports swap in use, but macOS keeps old swap around for hours, so that number says little. What
shows a model being squeezed is swap *activity* (bytes moved in and out of swap, as counters Prometheus
turns into a rate) and macOS's own memory-pressure level (percent of memory free, as `memory_pressure`
reports it). All read from vm_stat and sysctl, with no sudo, each time Prometheus asks.

Also macOS's thermal state (0 nominal, 1 fair, 2 serious, 3 critical), the one apps are told to slow
down by. It rises with heat well before the throttling warning that `pmset -g therm` reports, which stays
at 0 until macOS is actually cutting the chip's speed.

  python -m inference_vitals.sysstat --port 9092
"""

import argparse
import ctypes
import ctypes.util
import re
import subprocess
import time

METRICS = [
    "vitals_mac_swapped_in_bytes_total",
    "vitals_mac_swapped_out_bytes_total",
    "vitals_mac_paged_in_bytes_total",
    "vitals_mac_paged_out_bytes_total",
    "vitals_mac_memory_free_percent",
    "vitals_mac_swap_total_bytes",
    "vitals_mac_swap_used_bytes",
    "vitals_mac_thermal_state",
]
VM_FIELDS = {
    "Swapins": "swapped_in",
    "Swapouts": "swapped_out",
    "Pageins": "paged_in",
    "Pageouts": "paged_out",
}


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


def _read(name: str) -> str:
    cmd = ["vm_stat"] if name == "vm_stat" else ["sysctl", "-n", name]
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


class Sysstat:
    def __init__(self, read=_read, thermal=thermal_state):
        self.read, self.thermal = read, thermal

    def values(self) -> dict:
        v = {f"vitals_mac_{k}": n for k, n in parse_vm_stat(self.read("vm_stat")).items()}
        free = memory_free_percent(self.read("kern.memorystatus_level"))
        if free is not None:
            v["vitals_mac_memory_free_percent"] = free
        total, used = swap_usage(self.read("vm.swapusage"))
        v["vitals_mac_swap_total_bytes"], v["vitals_mac_swap_used_bytes"] = total, used
        state = self.thermal()
        if state is not None:
            v["vitals_mac_thermal_state"] = state
        return v

    def render(self) -> str:
        return "".join(f"{k} {v}\n" for k, v in self.values().items())

    def snapshot(self) -> str:
        import json

        return json.dumps(self.values())


def main():
    from inference_vitals.metrics import serve

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9092)
    server = serve(Sysstat(), p.parse_args().port)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
