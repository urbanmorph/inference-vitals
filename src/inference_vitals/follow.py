"""Follow a fine-tuning run from what its trainer prints, and publish it for the dashboard. The trainer
needs no change.

  inference-vitals train -- python -m mlx_vlm.train --model-path ... --data ...   wrap the command
  inference-vitals watch-training train.log                                     follow a log file

Read: mlx-vlm's trainer (metric blocks such as "Training (iteration 10):" then name=value pairs, its
progress bar, and "Saved adapter weights" lines) and mlx-lm's ("Iter 10: Train loss 1.23, ..."). Lines
it does not recognise change nothing. A trainer of your own can publish directly with
inference_vitals.training instead.
"""

import argparse
import os
import re
import subprocess
import sys
import time

from inference_vitals import training
from inference_vitals.metrics import DEFAULT_PORT

ANSI = re.compile(r"\x1b\[[0-9;]*m")
TOTAL = re.compile(r"Starting training\.\.\., (?:iterations|iters): (\d+)")
HEADER = re.compile(r"^(Training|Validation) \(iteration (\d+)\):")
PAIR = re.compile(r"\b([a-z_]+)=(-?[\d,]*\.?\d+(?:e[-+]?\d+)?)")
BAR = re.compile(r"\|\s*(\d+)/(\d+) \[[^\]]*?loss=([\d.]+)(?:, lr=([\d.e+-]+))?")
SAVED = re.compile(r"Saved (?:final )?(?:adapter|trainable) weights")  # mlx-lm; mlx-vlm 0.7
LM_TRAIN = re.compile(
    r"Iter (\d+): Train loss ([\d.]+), Learning Rate ([\d.e+-]+).*?Tokens/sec ([\d.]+)"
    r"(?:, Trained Tokens (\d+))?.*?Peak mem ([\d.]+) GB"
)
LM_VAL = re.compile(r"Iter (\d+): Val loss ([\d.]+)")
KEYS = {  # mlx-vlm's names -> training.Training.step's
    "train_loss": "loss",
    "learning_rate": "learning_rate",
    "tokens_per_second": "tokens_per_second",
    "peak_memory": "peak_memory_gb",
    "trained_tokens": "trained_tokens",
    "step_time": "step_seconds",  # its own timing: lines arrive in bursts, so timing them would mislead
}


class Reader:
    """Turns a trainer's printed lines into calls on a training.Training run."""

    def __init__(self, run: training.Training):
        self.run, self.block, self.step = run, None, 0

    def feed(self, text: str):
        for part in ANSI.sub("", text).split("\r"):
            self._part(part.strip())

    def _part(self, s: str):
        if not s:
            return
        run = self.run
        if m := TOTAL.search(s):
            run.total_steps = int(m.group(1))
        elif m := HEADER.match(s):
            self.block, self.step = m.group(1), int(m.group(2))
            if self.block == "Validation":
                run.validating()
        elif SAVED.search(s):
            run.saving()
        elif m := LM_VAL.search(s):
            run.validating()
            run.validate(float(m.group(2)))
        elif m := LM_TRAIN.search(s):
            step, loss, lr, tps, tokens, mem = m.groups()
            run.step(int(step), loss=float(loss), learning_rate=float(lr), tokens_per_second=float(tps),
                     peak_memory_gb=float(mem), trained_tokens=int(tokens) if tokens else None)  # fmt: skip
        elif m := BAR.search(s):
            step, total, loss, lr = m.groups()
            run.total_steps = run.total_steps or int(total)
            run.step(int(step), loss=float(loss), learning_rate=float(lr) if lr else None)
        elif self.block and (pairs := PAIR.findall(s)):
            nums = {k: float(v.replace(",", "")) for k, v in pairs}
            if self.block == "Validation":
                if "val_loss" in nums:
                    run.validate(nums["val_loss"])
            elif mapped := {KEYS[k]: v for k, v in nums.items() if k in KEYS}:
                run.step(self.step, **mapped)


def _parts(chunks):
    """The pieces of text between line ends and carriage returns, as chunks arrive."""
    buf = ""
    for chunk in chunks:
        buf += chunk
        *done, buf = re.split(r"[\r\n]", buf)
        yield from done
    if buf:
        yield buf


def train(
    cmd: list[str], name: str, port: int = DEFAULT_PORT, epochs=None, on_finish=None, linger=20.0
) -> int:
    """Run the trainer, pass its output through untouched, and publish what it reports while it runs."""
    run = training.start(name, epochs=epochs, port=port)
    reader = Reader(run)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env={**os.environ, "PYTHONUNBUFFERED": "1"})  # fmt: skip

    def chunks():
        while data := os.read(proc.stdout.fileno(), 4096):
            text = data.decode(errors="replace")
            sys.stdout.write(text)
            sys.stdout.flush()
            yield text

    try:
        for part in _parts(chunks()):
            reader.feed(part)
        return proc.wait()
    finally:
        run.finish(linger)
        if on_finish:
            on_finish(run)


def watch(path, run: training.Training, stop=lambda: False, poll=1.0):
    """Read a log file as it grows, until stop() says so at its end (Ctrl-C from the command line)."""
    reader = Reader(run)
    with open(path, errors="replace") as f:

        def chunks():
            while True:
                text = f.read()
                if text:
                    yield text
                elif stop():
                    return
                else:
                    time.sleep(poll)

        for part in _parts(chunks()):
            reader.feed(part)


def main(action: str, argv: list[str]):
    p = argparse.ArgumentParser(prog=f"inference-vitals {action}", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    p.add_argument("--name", help="the run's name on the dashboard (default: from the command or file)")
    p.add_argument("--epochs", type=int)
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="where the dashboard reads numbers")
    if action == "train":
        own, _, cmd = _split(argv)
        args = p.parse_args(own)
        if not cmd:
            p.error("give the training command after --")
        return train(cmd, args.name or os.path.basename(cmd[0]), args.port, args.epochs)
    p.add_argument("log", help="the trainer's log file")
    args = p.parse_args(argv)
    run = training.start(args.name or os.path.basename(args.log), epochs=args.epochs, port=args.port)
    try:
        watch(args.log, run)
    except KeyboardInterrupt:
        pass
    finally:
        run.finish()
    return 0


def _split(argv: list[str]):
    """(our options, "--", the trainer's command)."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], "--", argv[i + 1 :]
    return argv, "", []
