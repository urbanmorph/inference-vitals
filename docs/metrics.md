# Every metric inference-vitals exports

[← README](../README.md)

For anything else that reads Prometheus. All are served on `127.0.0.1` only. A test checks that this
list and the code agree, both ways. Chip numbers on a Mac come from [macmon](https://github.com/vladkens/macmon)
(`macmon_*`) and are not listed here.

## A model run

On `127.0.0.1:8400`, from the proxy or the library ([connect.md](connect.md)). Names follow vLLM's where
one exists. Labels: `model_name`.

| Metric | Meaning |
| --- | --- |
| `vllm:prompt_tokens_total` | Prompt tokens read, a counter |
| `vllm:generation_tokens_total` | Tokens written, a counter (live while an answer is written) |
| `vllm:num_requests_running` | Requests being answered now |
| `vllm:time_to_first_token_seconds` | Histogram of time to first token (warm answers) |
| `vllm:e2e_request_latency_seconds` | Histogram of whole-answer time |
| `vllm:time_per_output_token_seconds` | Histogram of seconds per written token |
| `vitals_current_phase` | 0 idle, 1 reading (prefill), 2 writing (decode) |
| `vitals_current_generation_tokens` | Tokens written so far in the current answer |
| `vitals_current_generation_tps` | Writing speed of the current answer |
| `vitals_current_context_tokens` | Context in use while writing (prompt plus tokens so far) |
| `vitals_pages_done`, `vitals_pages_total`, `vitals_pages_already_answered` | Progress through a run |
| `vitals_answers_cut_off_total` | Answers stopped at the token limit |
| `vitals_answers_invalid_total` | Answers not in the format asked for |
| `vitals_over_context_total` | Requests over the context limit |
| `vitals_requests_failed_total` | Requests the engine refused or that failed |
| `vitals_last_ttft_s`, `vitals_last_prompt_tps`, `vitals_last_generation_tps` | The last answer's speeds |
| `vitals_last_prompt_tokens`, `vitals_last_cached_tokens`, `vitals_last_generation_tokens` | The last answer's token counts |
| `vitals_last_context_used_tokens` | The last answer's context used |
| `vitals_last_thermal_warning`, `vitals_last_cpu_speed_limit` | The Mac's state at the last answer |
| `vitals_cold_ttft_s` | Time to first token of the first answer after loading |
| `vitals_model_load_seconds` | Time to load the model |
| `vitals_context_limit_tokens` | The context limit |
| `vitals_gpu_memory_limit_bytes` | The GPU memory limit the run reported |
| `vitals_active_memory_bytes`, `vitals_peak_memory_bytes`, `vitals_cache_memory_bytes` | The model's memory: in use, peak, kept for reuse |
| `vitals_kv_cache_estimate_bytes` | KV cache estimate for the last answer |
| `vitals_run_updated_seconds` | When the run last reported (the page shows whichever run reported last) |

## A fine-tune

On `127.0.0.1:8400`, from `train`, `watch-training` or the training library ([training.md](training.md)).
Labels: `run_name`.

| Metric | Meaning |
| --- | --- |
| `vitals_training_phase` | 0 idle (or finished), 1 step, 2 validating, 3 saving |
| `vitals_training_step_part` | Within a step, when reported: 1 read, 2 backward, 3 write |
| `vitals_training_step`, `vitals_training_total_steps` | Where the run is |
| `vitals_training_epoch`, `vitals_training_epochs` | The same, in epochs |
| `vitals_training_loss` | Training loss |
| `vitals_training_val_loss`, `vitals_training_val_loss_best` | Validation loss: the latest, and the best so far |
| `vitals_training_verdict` | 0 learning, 1 watch, 2 memorising (from two or more validation passes) |
| `vitals_training_learning_rate` | Learning rate |
| `vitals_training_tokens_per_second` | Training speed |
| `vitals_training_step_seconds` | Seconds per step |
| `vitals_training_tokens_total` | Tokens trained so far, a counter |
| `vitals_training_peak_memory_bytes` | The trainer's peak memory |
| `vitals_training_started_seconds` | When it started (the page's training charts span the run) |
| `vitals_training_updated_seconds` | When it last reported |

## The machine

On `127.0.0.1:9092` (both platforms) and, on Linux, `127.0.0.1:9091` (the NVIDIA exporter).

| Metric | Meaning |
| --- | --- |
| `vitals_host_swapped_in_bytes_total`, `vitals_host_swapped_out_bytes_total` | Swap activity, counters |
| `vitals_host_paged_in_bytes_total`, `vitals_host_paged_out_bytes_total` | Paging activity, counters |
| `vitals_host_memory_free_percent` | Free memory as the system measures it (memory pressure on a Mac) |
| `vitals_host_swap_total_bytes`, `vitals_host_swap_used_bytes` | Swap size and swap in use |
| `vitals_host_thermal_state` | Mac only: 0 nominal, 1 fair, 2 serious, 3 critical |
| `vitals_host_cpu_busy_ratio` | Linux only: CPU busy share |
| `vitals_host_cpu_temp_celsius` | Linux only: the CPU's own temperature |
| `vitals_battery_percent` | Laptops: charge |
| `vitals_battery_remaining_wh` | Laptops: energy left |
| `vitals_battery_on_power` | Laptops: 1 on mains power, 0 on battery |
| `vitals_battery_draw_watts` | Laptops, on battery only: what the battery is giving now |
| `vitals_gpu_busy_ratio` | Linux, per GPU: busy share |
| `vitals_gpu_memory_total_bytes`, `vitals_gpu_memory_used_bytes`, `vitals_gpu_memory_free_bytes` | Linux, per GPU: VRAM |
| `vitals_gpu_temp_celsius` | Linux, per GPU: temperature |
| `vitals_gpu_power_watts`, `vitals_gpu_power_limit_watts` | Linux, per GPU: power and its limit |
| `vitals_gpu_freq_mhz`, `vitals_gpu_memory_freq_mhz` | Linux, per GPU: clocks |
| `vitals_gpu_fan_ratio` | Linux, per GPU: fan speed |
| `vitals_gpu_slowdown_state` | Linux, per GPU: 0 none, 1 power cap, 2 thermal, 3 hardware |
