"""The Mac's memory as Prometheus numbers, all the time, on 127.0.0.1:9092 (started by inference-vitals).

macmon reports swap in use, but macOS keeps old swap around for hours, so that number says little. What
shows a model being squeezed is swap *activity* (bytes moved in and out of swap, as counters Prometheus
turns into a rate) and macOS's own memory-pressure level (percent of memory free, as `memory_pressure`
reports it). All read from vm_stat and sysctl, with no sudo, each time Prometheus asks.

  python -m inference_vitals.sysstat --port 9092
"""

import argparse
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


def _read(name: str) -> str:
    cmd = ["vm_stat"] if name == "vm_stat" else ["sysctl", "-n", name]
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


class Sysstat:
    def __init__(self, read=_read):
        self.read = read

    def values(self) -> dict:
        v = {f"vitals_mac_{k}": n for k, n in parse_vm_stat(self.read("vm_stat")).items()}
        free = memory_free_percent(self.read("kern.memorystatus_level"))
        if free is not None:
            v["vitals_mac_memory_free_percent"] = free
        total, used = swap_usage(self.read("vm.swapusage"))
        v["vitals_mac_swap_total_bytes"], v["vitals_mac_swap_used_bytes"] = total, used
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
