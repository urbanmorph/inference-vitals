import json
import urllib.request

from inference_vitals import training


def _values(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        name, value = line.rsplit(" ", 1)
        out[name.split("{")[0]] = float(value)
    return out


def test_a_run_publishes_its_steps_losses_speed_and_memory():
    run = training.Training(
        "rf-1", total_steps=200, epochs=2, clock=iter([100.0, 103.5, 104, 105, 106]).__next__
    )
    run.step(1, loss=2.1, learning_rate=1e-4)
    run.step(2, loss=1.9, learning_rate=1e-4, tokens_per_second=812.5, peak_memory_gb=7.25, epoch=1)
    m = _values(run.render())
    assert m["vitals_training_step"] == 2 and m["vitals_training_total_steps"] == 200
    assert m["vitals_training_loss"] == 1.9 and m["vitals_training_learning_rate"] == 1e-4
    assert m["vitals_training_tokens_per_second"] == 812.5
    assert m["vitals_training_peak_memory_bytes"] == 7.25e9
    assert m["vitals_training_step_seconds"] == 3.5  # between the two steps
    assert m["vitals_training_epoch"] == 1 and m["vitals_training_epochs"] == 2
    assert m["vitals_training_phase"] == training.PHASES["step"]
    assert "vitals_training_val_loss" not in m  # not reported until a validation pass has run
    assert 'run_name="rf-1"' in run.render()


def test_validation_and_saving_are_phases_and_the_best_loss_is_kept():
    run = training.Training("rf-1")
    run.validating()
    assert _values(run.render())["vitals_training_phase"] == training.PHASES["validate"]
    run.validate(1.1)
    run.validate(0.95)
    run.saving()
    m = _values(run.render())
    assert m["vitals_training_val_loss"] == 0.95 and m["vitals_training_val_loss_best"] == 0.95
    assert m["vitals_training_phase"] == training.PHASES["save"]
    run.finish()
    assert _values(run.render())["vitals_training_phase"] == training.PHASES["idle"]


def test_a_step_can_say_which_part_it_is_in_when_the_trainer_knows():
    run = training.Training("rf-1")
    run.step(5, loss=1.0)
    run.part("backward")
    m = _values(run.render())
    assert m["vitals_training_phase"] == training.PHASES["step"] and m["vitals_training_step_part"] == 2


def test_the_verdict_learning_watch_or_memorising_follows_the_validation_loss():
    run = training.Training("rf-1")
    run.step(1, loss=2.0)
    assert "vitals_training_verdict" not in _values(run.render())  # nothing to judge yet
    run.validate(1.2)
    run.step(2, loss=1.5)
    run.validate(1.0)
    assert _values(run.render())["vitals_training_verdict"] == training.LEARNING
    run.step(3, loss=1.2)
    run.validate(1.0)  # no better than the best
    assert _values(run.render())["vitals_training_verdict"] == training.WATCH
    run.step(4, loss=0.8)  # train loss still falling...
    run.validate(1.2)  # ...while validation rises
    assert _values(run.render())["vitals_training_verdict"] == training.MEMORISING


def test_the_last_update_time_lets_the_page_tell_training_from_inference():
    run = training.Training("rf-1", clock=lambda: 1000.0, wall=lambda: 1_700_000_000.0)
    run.step(1, loss=1.0)
    assert _values(run.render())["vitals_training_updated_seconds"] == 1_700_000_000.0


def test_start_serves_the_numbers_on_this_machine_only():
    run = training.start("rf-1", total_steps=10, port=0)
    try:
        run.step(1, loss=1.5)
        port = run.server.server_address[1]
        assert run.server.server_address[0] == "127.0.0.1"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as r:
            assert "vitals_training_loss" in r.read().decode()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics.json", timeout=5) as r:
            assert json.loads(r.read())["loss"] == 1.5
    finally:
        run.finish(linger=0)


def test_trained_tokens_count_up_so_energy_per_trained_token_can_be_worked_out():
    run = training.Training("rf-1")
    run.step(1, trained_tokens=3000)
    run.step(2, trained_tokens=6100)
    assert _values(run.render())["vitals_training_tokens_total"] == 6100


def test_finish_waits_until_the_final_state_has_been_read_or_a_time_limit():
    import threading

    run = training.start("rf-1", port=0)
    run.step(1, loss=1.0)
    port = run.server.server_address[1]
    reader = threading.Timer(
        0.3, lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read()
    )
    reader.start()
    started = __import__("time").monotonic()
    run.finish(linger=10)
    waited = __import__("time").monotonic() - started
    assert 0.2 < waited < 5  # it waited for the read, not the whole 10 s
    quick = training.start("rf-2", port=0)
    t0 = __import__("time").monotonic()
    quick.finish(linger=0.2)  # nobody reads: it gives up at the limit
    assert __import__("time").monotonic() - t0 < 2


def test_a_trainer_that_times_its_own_steps_is_believed():
    run = training.Training("rf-1", clock=iter([0.0, 0.0]).__next__)
    run.step(5, loss=1.0, step_seconds=6.2)
    assert _values(run.render())["vitals_training_step_seconds"] == 6.2


def test_a_run_says_when_it_started_so_its_charts_can_span_just_the_run():
    run = training.Training("rf-1", wall=lambda: 1_700_000_000.0)
    assert _values(run.render())["vitals_training_started_seconds"] == 1_700_000_000.0


def test_a_tie_with_a_validation_before_any_training_step_does_not_break_the_numbers():
    run = training.Training("rf-1")
    run.validate(2.5)  # mlx-lm validates at step 1, before its first training line: no train loss yet
    run.step(10, loss=2.0)
    run.validate(2.5)  # a tie, as 3-decimal output makes likely
    run.validate(2.6)
    assert _values(run.render())["vitals_training_verdict"] == training.WATCH


def test_a_run_name_with_a_quote_or_a_backslash_is_escaped():
    text = training.Training('a"b\\c\nd').render()
    assert 'run_name="a\\"b\\\\c\\nd"' in text


def test_each_run_has_its_own_id_so_two_runs_with_one_name_never_mix():
    a = training.Training("python", wall=lambda: 1_700_000_000.0).render()
    b = training.Training("python", wall=lambda: 1_700_000_500.0).render()
    assert 'run_id="1700000000"' in a and 'run_id="1700000500"' in b


def test_the_step_part_reads_zero_when_none_is_reported_rather_than_keeping_the_last():
    run = training.Training("rf-1")
    run.step(1, loss=1.0)
    run.part("write")
    run.step(2, loss=0.9)  # a new step: no part reported yet
    assert _values(run.render())["vitals_training_step_part"] == 0


def test_a_live_run_says_it_is_alive_each_time_it_is_read():
    run = training.Training("rf-1", wall=iter([1_700_000_000.0, 1_700_000_042.0]).__next__)
    assert (
        _values(run.render())["vitals_training_alive_seconds"] == 1_700_000_042.0
    )  # read time, not event time


def test_a_watched_log_that_stops_growing_marks_the_run_idle(tmp_path):
    from inference_vitals import follow

    log = tmp_path / "train.log"
    log.write_text(
        "Iter 10: Train loss 1.234, Learning Rate 1.000e-05, It/sec 0.5, Tokens/sec 412.3, Peak mem 7.5 GB\n"
    )
    run = training.Training("t")
    ticks = iter([0, 0, 400, 400, 400])
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 2

    follow.watch(log, run, stop=stop, poll=0, clock=lambda: next(ticks), idle_after=300)
    assert run.phase == training.PHASES["idle"]  # no new lines for 400 s: not "Step" for a week
