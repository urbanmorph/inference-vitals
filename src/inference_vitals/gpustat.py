"""NVIDIA GPUs as Prometheus numbers on 127.0.0.1:9091 (started by inference-vitals on Linux), read from
`nvidia-smi`, which comes with every NVIDIA driver. No sudo, nothing else to install.

Each GPU gets its own labels (gpu="0", name="NVIDIA GeForce RTX 4090"). A value a GPU does not report
("[N/A]", common for power and fan on laptops and some server cards) is left out, not sent as zero.

Slowdown state, the GPU's own reasons for running below full clock speed, worst first:
  0 none · 1 power cap (normal under full load) · 2 thermal slowdown · 3 hardware slowdown (power brake,
  external signal). Newer drivers call these `clocks_event_reasons`, older ones `clocks_throttle_reasons`;
  both are tried.

  python -m inference_vitals.gpustat --port 9091
"""

import argparse
import shutil
import subprocess

from inference_vitals.metrics import labels as label_text

FIELDS = [
    "index",
    "name",
    "utilization.gpu",
    "memory.total",
    "memory.used",
    "memory.free",
    "temperature.gpu",
    "power.draw",
    "power.limit",
    "clocks.gr",
    "clocks.mem",
    "fan.speed",
]
MIB = 2**20
# field -> (metric, multiplier)
NUMBERS = {
    "utilization.gpu": ("vitals_gpu_busy_ratio", 0.01),
    "memory.total": ("vitals_gpu_memory_total_bytes", MIB),
    "memory.used": ("vitals_gpu_memory_used_bytes", MIB),
    "memory.free": ("vitals_gpu_memory_free_bytes", MIB),
    "temperature.gpu": ("vitals_gpu_temp_celsius", 1),
    "power.draw": ("vitals_gpu_power_watts", 1),
    "power.limit": ("vitals_gpu_power_limit_watts", 1),
    "clocks.gr": ("vitals_gpu_freq_mhz", 1),
    "clocks.mem": ("vitals_gpu_memory_freq_mhz", 1),
    "fan.speed": ("vitals_gpu_fan_ratio", 0.01),
}
REASONS = [
    "sw_power_cap",
    "sw_thermal_slowdown",
    "hw_thermal_slowdown",
    "hw_slowdown",
    "hw_power_brake_slowdown",
]
REASON_PREFIXES = ["clocks_event_reasons", "clocks_throttle_reasons"]  # newer drivers first
METRICS = [m for m, _ in NUMBERS.values()] + ["vitals_gpu_slowdown_state"]


def slowdown_state(active: dict) -> int:
    if active.get("hw_slowdown") or active.get("hw_power_brake_slowdown"):
        return 3
    if active.get("sw_thermal_slowdown") or active.get("hw_thermal_slowdown"):
        return 2
    return 1 if active.get("sw_power_cap") else 0


def _rows(text: str) -> list[list[str]]:
    return [[c.strip() for c in line.split(",")] for line in text.splitlines() if line.strip()]


def _number(s: str):
    try:
        return float(s)
    except ValueError:  # "[N/A]", "[Not Supported]"
        return None


def _run(args):
    p = subprocess.run(args, capture_output=True, text=True, check=False, timeout=10)
    return p.returncode, p.stdout, p.stderr


class NvidiaGpus:
    def __init__(self, run=_run, smi="nvidia-smi"):
        self.run, self.smi = run, smi
        self.reasons_prefix = None  # found on first use; False once neither name works

    def _query(self, fields):
        code, out, _ = self.run(
            [self.smi, f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"]
        )
        return _rows(out) if code == 0 else None

    def _reasons(self) -> dict:
        """gpu index -> {reason: active}; {} if this driver reports none."""
        prefixes = [self.reasons_prefix] if self.reasons_prefix else REASON_PREFIXES
        if self.reasons_prefix is False:
            return {}
        for prefix in prefixes:
            rows = self._query(["index"] + [f"{prefix}.{r}" for r in REASONS])
            if rows is not None:
                self.reasons_prefix = prefix
                return {row[0]: {r: v == "Active" for r, v in zip(REASONS, row[1:])} for row in rows}
        self.reasons_prefix = False
        return {}

    def values(self) -> list[tuple[str, dict, float]]:
        rows = self._query(FIELDS)
        if not rows:
            return []
        reasons = self._reasons()
        out = []
        for row in rows:
            got = dict(zip(FIELDS, row))
            labels = {"gpu": got["index"], "name": got["name"]}
            for field, (metric, scale) in NUMBERS.items():
                n = _number(got.get(field, ""))
                if n is not None:
                    out.append((metric, labels, round(n * scale, 4) if scale != MIB else int(n * scale)))
            if got["index"] in reasons:
                out.append(("vitals_gpu_slowdown_state", labels, slowdown_state(reasons[got["index"]])))
        return out

    def render(self) -> str:
        return "".join(
            f"{metric}{label_text({'gpu': lab['gpu'], 'name': lab['name']})} {value}\n"
            for metric, lab, value in self.values()
        )

    def snapshot(self) -> str:
        import json

        return json.dumps([{"metric": m, **lab, "value": v} for m, lab, v in self.values()])


def main():
    from inference_vitals.metrics import run_until_interrupt, serve

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9091)
    server = serve(NvidiaGpus(smi=shutil.which("nvidia-smi") or "nvidia-smi"), p.parse_args().port)
    run_until_interrupt(server)


if __name__ == "__main__":
    main()
