"""The dashboard's vocabulary: the PromQL both dashboards (the page in ui.py, Grafana in grafana.py) ask
Prometheus, what each platform measures the machine with, and the names of each state's values."""

MAC, LINUX = "mac", "linux"
PLATFORMS = (MAC, LINUX)

# What macmon 0.10 publishes (checked against `macmon serve` on 2026-11-05).
MACMON_METRICS = [
    "macmon_cpu_temp_celsius",
    "macmon_gpu_temp_celsius",
    "macmon_memory_ram_total_bytes",
    "macmon_memory_ram_used_bytes",
    "macmon_memory_swap_total_bytes",
    "macmon_memory_swap_used_bytes",
    "macmon_cpu_usage_ratio",
    "macmon_cpu_active_ratio",
    "macmon_ecpu_freq_mhz",
    "macmon_ecpu_active_ratio",
    "macmon_pcpu_freq_mhz",
    "macmon_pcpu_active_ratio",
    "macmon_gpu_freq_mhz",
    "macmon_gpu_usage_ratio",
    "macmon_gpu_active_ratio",
    "macmon_cpu_power_watts",
    "macmon_gpu_power_watts",
    "macmon_ane_power_watts",
    "macmon_all_power_watts",
    "macmon_sys_power_watts",
    "macmon_ram_power_watts",
    "macmon_fan_speed_rpm",
]


THERMAL_NAMES = ["Nominal", "Fair", "Serious", "Critical"]
PHASE_NAMES = ["Idle", "Reading (prefill)", "Writing (decode)"]  # metrics.IDLE, READING, WRITING


TTFT_Q = (
    "histogram_quantile({q}, sum by (le, model_name) (rate(vllm:time_to_first_token_seconds_bucket[10m])))"
)
SLOWDOWN_NAMES = ["None", "Power cap", "Thermal", "Hardware"]  # gpustat.slowdown_state
# Energy: the model's share is the power above the machine's usual idle draw (the 10th percentile of the last
# day's power, which follows the screen and background apps) while a model is reading or writing. Energy per
# token is that share divided by the tokens written, here over a rolling 2 minutes; the page's band and tile
# use the same split over the chosen range, so every energy figure agrees.
# 1 while a model is reading or writing, or a fine-tune is training, else 0 (either may not be reporting)
RUN_ACTIVE = "((max(vitals_current_phase) > bool 0) or vector(0))"  # 1 while a model reads or writes
TRAIN_ACTIVE = "((max(vitals_training_phase) > bool 0) or vector(0))"  # 1 while a fine-tune trains
ACTIVE = f"clamp_max({RUN_ACTIVE} + {TRAIN_ACTIVE}, 1)"  # either: the AI's share of the energy


def idle_power(meter: str) -> str:
    """The machine's usual idle draw: the low end (10th percentile) of the last day's power, sampled every
    minute, so a fresh install has a baseline within a minute. Prometheus works it out once a minute as
    vitals:idle_power_watts (rules_config), since a look-back over a day is costly to repeat on each chart."""
    return f"quantile_over_time(0.1, ({meter})[24h:1m])"


IDLE = "max(vitals:idle_power_watts)"  # idle_power, as Prometheus records it once a minute


def rules_config(platform: str) -> str:
    return f"""groups:
  - name: inference-vitals
    interval: 1m
    rules:
      - record: vitals:idle_power_watts
        expr: {idle_power(HW[platform]["meter"])}
"""


def ai_joules_per_token(meter: str) -> str:
    share = f"clamp_min({meter} - scalar({IDLE}), 0) * {RUN_ACTIVE}"  # answering only
    return f"sum(avg_over_time(({share})[2m:15s]) / (sum(rate(vllm:generation_tokens_total[2m])) > 0))"


# What each platform measures the machine with. The model half of the dashboard is the same everywhere.
HW = {
    MAC: {
        # One machine, so max(): a tile shows one number even if Prometheus briefly holds two copies.
        "gpu_busy": "max(macmon_gpu_active_ratio)",
        "gpu_temp": "max(macmon_gpu_temp_celsius)",
        # the tightest model's own peak against the GPU limit, when a run reports them; otherwise the
        # memory left on the machine (Apple Silicon: the GPU and the rest share one memory)
        "gpu_mem_left": "min(vitals_gpu_memory_limit_bytes - vitals_peak_memory_bytes) or "
        "(max(macmon_memory_ram_total_bytes) - max(macmon_memory_ram_used_bytes))",
        "slowdown": ("Thermal", "max(vitals_host_thermal_state)", THERMAL_NAMES, 1, 2),
        "power": ("Mac power", "max(macmon_sys_power_watts)"),
        "meter": "max(macmon_sys_power_watts)",  # the whole Mac
        "joules": ai_joules_per_token("max(macmon_sys_power_watts)"),
    },
    LINUX: {
        "gpu_busy": "max(vitals_gpu_busy_ratio)",
        "gpu_temp": "max(vitals_gpu_temp_celsius)",
        "gpu_mem_left": "sum(vitals_gpu_memory_free_bytes)",
        "slowdown": ("GPU slowdown", "max(vitals_gpu_slowdown_state)", SLOWDOWN_NAMES, 2, 3),
        "power": ("GPU power", "sum(vitals_gpu_power_watts)"),
        "meter": "sum(vitals_gpu_power_watts)",  # the GPUs only: Linux has no whole-machine meter
        "joules": ai_joules_per_token("sum(vitals_gpu_power_watts)"),
    },
}
SWAP_RATE = (
    "max(rate(vitals_host_swapped_in_bytes_total[1m])) + max(rate(vitals_host_swapped_out_bytes_total[1m]))"
)
# live while a page is written (page + tokens so far); between pages, the last page in full
CONTEXT = "(vitals_current_context_tokens or vitals_last_context_used_tokens) / vitals_context_limit_tokens"

# Metrics that are a ceiling: a chart that draws one ends its axis there (the machine's own memory, not a
# round number above it), so the lines below it read against the real limit.
LIMITS = (
    "macmon_memory_ram_total_bytes",
    "vitals_gpu_memory_limit_bytes",
    "vitals_host_gpu_memory_limit_bytes",
    "vitals_gpu_memory_total_bytes",
    "vitals_gpu_power_limit_watts",
    "vitals_context_limit_tokens",
)
