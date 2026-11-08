"""Publish what a fine-tuning run is doing, for the dashboard: the same /metrics page on 127.0.0.1 as a
model run, with vitals_training_* numbers. Numbers only: no text, file name or data ever reaches it.

    from inference_vitals import training
    run = training.start("my-adapter", total_steps=400, epochs=2)
    for step in ...:
        ...
        run.step(step, loss=loss, learning_rate=lr, tokens_per_second=tps, peak_memory_gb=mem)
    run.validating(); run.validate(val_loss)      # each validation pass
    run.saving()                                  # while a checkpoint is written
    run.finish()

A trainer that knows which part of a step it is in can say so with run.part("read" | "backward" |
"write"); most only report whole steps, and the page then shows "step". The run also says whether it is
still learning: validation loss still falling (learning), not better than its best (watch), or rising
while the training loss still falls (memorising the examples rather than learning from them).
"""

import json
import threading
import time

from inference_vitals.metrics import DEFAULT_PORT, serve

PHASES = {"idle": 0, "step": 1, "validate": 2, "save": 3}
PARTS = {"read": 1, "backward": 2, "write": 3}
LEARNING, WATCH, MEMORISING = 0, 1, 2
RISE = 0.02  # validation loss this far above its best (2%) counts as rising, not noise


class Training:
    def __init__(self, name: str, total_steps=None, epochs=None, clock=time.monotonic, wall=time.time):
        self.name, self.total_steps, self.epochs = name, total_steps, epochs
        self.clock, self.wall = clock, wall
        self.lock = threading.Lock()
        self.phase, self.step_part = PHASES["idle"], None
        self.now: dict = {}  # the latest of each number a step reported
        self.vals: list[tuple[float, float | None]] = []  # (validation loss, training loss at that time)
        self.last_step_at = None
        self.updated = None
        self.started = wall()  # so the page's training charts can span just this run
        self.was_read = threading.Event()  # set each time the page is read: finish() waits for the last one

    def _touch(self):
        self.updated = self.wall()

    def step(self, step: int, loss=None, learning_rate=None, tokens_per_second=None, peak_memory_gb=None,
             epoch=None, trained_tokens=None, step_seconds=None):  # fmt: skip
        with self.lock:
            prev = self.now.get("step")
            if prev is None or step != prev:  # a trainer may report one step in several lines
                at = self.clock()
                if prev is not None and step > prev and self.last_step_at is not None:
                    self.now["step_seconds"] = round((at - self.last_step_at) / (step - prev), 3)
                self.last_step_at = at
            reported = {"step": step, "loss": loss, "learning_rate": learning_rate,
                        "tokens_per_second": tokens_per_second, "peak_memory_gb": peak_memory_gb, "epoch": epoch,
                        "trained_tokens": trained_tokens, "step_seconds": step_seconds}  # fmt: skip
            self.now.update({k: v for k, v in reported.items() if v is not None})
            self.phase, self.step_part = PHASES["step"], None
            self._touch()

    def part(self, name: str):
        with self.lock:
            self.phase, self.step_part = PHASES["step"], PARTS[name]
            self._touch()

    def validating(self):
        with self.lock:
            self.phase, self.step_part = PHASES["validate"], None
            self._touch()

    def validate(self, val_loss: float):
        with self.lock:
            self.vals.append((val_loss, self.now.get("loss")))
            self._touch()

    def saving(self):
        with self.lock:
            self.phase, self.step_part = PHASES["save"], None
            self._touch()

    def finish(self, linger: float = 20.0):
        """Mark the run finished. If it is being served, keep serving until the dashboard has read that
        (Prometheus reads every 15 s), or `linger` seconds, so a short run does not end mid-step there."""
        with self.lock:
            self.phase, self.step_part = PHASES["idle"], None
            self._touch()
            self.was_read.clear()
        if getattr(self, "server", None):
            self.was_read.wait(linger)
            self.server.shutdown()
            self.server.server_close()

    def verdict(self):
        """LEARNING, WATCH or MEMORISING from the validation losses so far; None before there are two."""
        if len(self.vals) < 2:
            return None
        last, train_now = self.vals[-1]
        best, train_at_best = min(self.vals[:-1])
        if last < best:
            return LEARNING
        falling = train_now is not None and train_at_best is not None and train_now < train_at_best
        return MEMORISING if last > best * (1 + RISE) and falling else WATCH

    def render(self) -> str:
        self.was_read.set()
        with self.lock:
            n = self.now
            gauges = [
                ("vitals_training_phase", self.phase),
                ("vitals_training_started_seconds", self.started),
            ]
            if self.step_part is not None:
                gauges.append(("vitals_training_step_part", self.step_part))
            for key, name in (("step", "step"), ("loss", "loss"), ("learning_rate", "learning_rate"),
                              ("tokens_per_second", "tokens_per_second"), ("step_seconds", "step_seconds"),
                              ("epoch", "epoch")):  # fmt: skip
                if key in n:
                    gauges.append((f"vitals_training_{name}", n[key]))
            if "trained_tokens" in n:  # a counter: energy per trained token is power over its increase
                gauges.append(("vitals_training_tokens_total", n["trained_tokens"]))
            if "peak_memory_gb" in n:
                gauges.append(("vitals_training_peak_memory_bytes", round(n["peak_memory_gb"] * 1e9)))
            if self.total_steps is not None:
                gauges.append(("vitals_training_total_steps", self.total_steps))
            if self.epochs is not None:
                gauges.append(("vitals_training_epochs", self.epochs))
            if self.vals:
                gauges += [("vitals_training_val_loss", self.vals[-1][0]),
                           ("vitals_training_val_loss_best", min(v for v, _ in self.vals))]  # fmt: skip
            verdict = self.verdict()
            if verdict is not None:
                gauges.append(("vitals_training_verdict", verdict))
            if self.updated is not None:
                gauges.append(("vitals_training_updated_seconds", self.updated))
            return "".join(f'{name}{{run_name="{self.name}"}} {value}\n' for name, value in gauges)

    def snapshot(self) -> str:
        with self.lock:
            out = {"run_name": self.name, "phase": self.phase, "total_steps": self.total_steps, **self.now}
            if self.vals:
                out["val_loss"] = self.vals[-1][0]
            return json.dumps(out)


def start(name: str, total_steps=None, epochs=None, port: int = DEFAULT_PORT) -> Training:
    """A run, served at once on 127.0.0.1:port (the port the dashboard reads; 0 picks a free one)."""
    run = Training(name, total_steps, epochs)
    run.server = serve(run, port)
    return run
