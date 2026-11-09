import sys

from inference_vitals import follow, training

# As mlx-vlm 0.7's trainer prints (trainer/common/engine.py, metrics.format_metric_report): a progress bar
# redrawn with \r, metric blocks of name=value pairs under a header, colour codes, and saving lines.
MLX_VLM = (
    "\x1b[95mStarting training..., iterations: 362\x1b[0m\n"
    "Training:   0%|          | 0/362 [00:00<?, ?it/s]\r"
    "Training:   3%|▎         | 10/362 [01:02<36:24, 6.21s/it, loss=1.8123, lr=1.00e-04]\n"
    "Training (iteration 10):\n"
    "train_loss=1.8123, learning_rate=0.0001, iterations_per_second=0.161, tokens_per_second=512.4\n"
    "trained_tokens=31,940, step_time=6.2, remaining_time=2185.3, progress=0.027624\n"
    "peak_memory=11.62, active_memory=6.1, cache_memory=4.2\n"
    "Validation (iteration 10):\n"
    "val_loss=1.9001, val_time=21.4\n"
    "\x1b[94mIter 50: Saved adapter weights to adapters.safetensors and 0000050_adapters.safetensors.\x1b[0m\n"
)
# As mlx-lm's LoRA trainer prints.
MLX_LM = (
    "Starting training..., iters: 600\n"
    "Iter 1: Val loss 2.345, Val took 1.234s\n"
    "Iter 10: Train loss 1.234, Learning Rate 1.000e-05, It/sec 0.512, Tokens/sec 412.3, "
    "Trained Tokens 8123, Peak mem 7.512 GB\n"
    "Iter 100: Saved adapter weights to adapters/adapters.safetensors.\n"
)


def _fed(text, **kw):
    run = training.Training("t", **kw)
    reader = follow.Reader(run)
    for line in text.splitlines(keepends=True):
        reader.feed(line)
    return run


def test_mlx_vlm_output_gives_the_steps_losses_speed_and_memory():
    run = _fed(MLX_VLM)
    assert run.total_steps == 362
    n = run.now
    assert n["step"] == 10 and n["loss"] == 1.8123 and n["learning_rate"] == 0.0001
    assert n["tokens_per_second"] == 512.4 and n["peak_memory_gb"] == 11.62
    assert run.vals == [(1.9001, 1.8123)]
    assert run.phase == training.PHASES["save"]  # the last line said it was saving


def test_mlx_lm_output_is_read_the_same_way():
    run = _fed(MLX_LM)
    assert run.total_steps == 600
    assert run.now["step"] == 10 and run.now["loss"] == 1.234 and run.now["tokens_per_second"] == 412.3
    assert run.now["peak_memory_gb"] == 7.512 and run.vals == [(2.345, None)]


def test_lines_it_does_not_know_change_nothing():
    run = _fed("loading weights\nsome warning: x=1\nIter: nothing\n")
    assert run.now == {} and run.vals == [] and run.phase == training.PHASES["idle"]


def test_train_wraps_a_command_shows_its_output_and_publishes_while_it_runs(capsys):
    script = "import sys; print('Starting training..., iterations: 2'); print('Training (iteration 2):'); print('train_loss=0.5')"
    published = []
    code = follow.train(
        [sys.executable, "-c", script], name="t", port=0, on_finish=published.append, linger=0
    )
    assert code == 0
    assert "train_loss=0.5" in capsys.readouterr().out  # the trainer's own output, untouched
    run = published[0]
    assert run.now["loss"] == 0.5 and run.total_steps == 2 and run.phase == training.PHASES["idle"]


def test_watch_reads_a_log_file_to_its_end(tmp_path):
    log = tmp_path / "train.log"
    log.write_text(MLX_LM)
    run = training.Training("t")
    follow.watch(log, run, stop=lambda: True)  # one pass over what is there, then stop
    assert run.now["step"] == 10 and run.total_steps == 600


def test_trained_tokens_are_read_from_both_trainers():
    assert _fed(MLX_VLM).now["trained_tokens"] == 31940
    assert _fed(MLX_LM).now["trained_tokens"] == 8123


# Captured from a real 6-step LoRA run of mlx-vlm 0.7.12 on made-up images (`inference-vitals train --
# python -m mlx_vlm.train ...`, 2026-11-08): the format as it really is, not as its source suggests.
MLX_VLM_LIVE = """Training:  17%|█▋        | 1/6 [00:05<00:27,  5.51s/it, loss=3.4738, lr=1.00e-05]
Training (iteration 6):
optimizer_step=6, train_loss=1.7783, train_nll=1.7783, train_token_accuracy=0.77273
train_num_padded_tokens=34, train_num_processed_tokens=220, train_num_sequences=2, train_num_tokens=22
train_num_vision_tokens=160, train_perplexity=5.9195, learning_rate=1e-05, iterations_per_second=3.245
optimizer_steps_per_second=3.245, tokens_per_second=35.695, trained_tokens=66, step_time=0.30817
data_time_per_step=0.00035962, train_time=6.2016, data_time=1.0724, elapsed_time=8.0901
remaining_time=0, progress=1, peak_memory=4.724, active_memory=3.2203
Validation (iteration 6):
val_loss=0.83765, val_nll=0.83765, val_token_accuracy=0.81818, val_num_padded_tokens=17
Saved final trainable weights to adapter/adapters.safetensors.
"""


def test_a_real_mlx_vlm_run_is_read_in_full():
    run = _fed(MLX_VLM_LIVE)
    n = run.now
    assert n["step"] == 6 and n["loss"] == 1.7783 and n["learning_rate"] == 1e-05
    assert n["tokens_per_second"] == 35.695 and n["trained_tokens"] == 66 and n["peak_memory_gb"] == 4.724
    assert run.total_steps == 6  # from the progress bar: this version prints no "Starting training" line
    assert run.vals[-1][0] == 0.83765
    assert run.phase == training.PHASES["save"]  # "Saved final trainable weights"


def test_train_passes_the_trainers_bytes_through_even_when_a_character_spans_two_reads(capfdbinary):
    script = "import sys; sys.stdout.buffer.write(('█' * 3000 + '\\n').encode()); sys.stdout.flush()"
    follow.train([sys.executable, "-c", script], name="t", port=0, linger=0)
    out = capfdbinary.readouterr().out
    assert ("█" * 3000).encode() in out and "�".encode() not in out


def test_a_run_is_named_after_the_module_it_runs_not_after_python():
    assert follow.default_name(["python", "-m", "mlx_vlm.train", "--model", "x"]) == "mlx_vlm.train"
    assert follow.default_name(["/usr/bin/python3", "train.py"]) == "train.py"
    assert follow.default_name(["./my-trainer", "--epochs", "2"]) == "my-trainer"


def test_a_progress_bar_with_no_loss_yet_still_moves_the_run():
    # mlx-vlm on an 8B: steps are slow and the bar shows no loss until the first report (every 10 steps)
    run = _fed(
        "Training:   0%|          | 0/181 [00:00<?, ?it/s]\r"
        "Training:   1%|          | 1/181 [01:42<5:08:55, 102.98s/it]\r"
        "Training:   1%|          | 2/181 [03:02<4:26:04, 89.19s/it] \r"
    )
    assert run.total_steps == 181 and run.now["step"] == 2
    assert "loss" not in run.now  # nothing invented
    assert run.updated is not None  # so the page switches to it at once, not ten steps in
