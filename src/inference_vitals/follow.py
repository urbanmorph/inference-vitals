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
import codecs
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
# a tqdm progress bar: "| 12/181 [..., loss=0.41, lr=1.00e-04]"; the loss and rate only once reported
RATE = re.compile(r"([\d.]+)(s/it|it/s)")  # the bar's own pace: seconds a step, or steps a second
BAR = re.compile(r"\|\s*(\d+)/(\d+) \[[^\]]*?(?:loss=([\d.]+)(?:, lr=([\d.e+-]+))?)?\]?\s*$")
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
            rate = RATE.search(s)  # lines may arrive in bursts, so the gaps between them say little
            secs = None if not rate else float(rate[1]) if rate[2] == "s/it" else round(1 / float(rate[1]), 3)
            run.step(
                int(step),
                loss=float(loss) if loss else None,
                learning_rate=float(lr) if lr else None,
                step_seconds=secs,
            )
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

    decode = codecs.getincrementaldecoder("utf-8")(errors="replace").decode  # a character may span two reads

    def chunks():
        while data := os.read(proc.stdout.fileno(), 4096):
            sys.stdout.buffer.write(data)  # the trainer's own bytes, untouched
            sys.stdout.buffer.flush()
            yield decode(data)

    try:
        for part in _parts(chunks()):
            reader.feed(part)
        return proc.wait()
    except KeyboardInterrupt:  # Ctrl-C: stop the trainer too, and do not wait long for the last read
        proc.terminate()
        proc.wait(10)
        linger = min(linger, 5)
        return 130
    finally:
        run.finish(linger)
        if on_finish:
            on_finish(run)


IDLE_AFTER = 300  # seconds without a new line before a watched run counts as no longer training


def watch(
    path, run: training.Training, stop=lambda: False, poll=1.0, clock=time.monotonic, idle_after=IDLE_AFTER
):
    """Read a log file as it grows, until stop() says so at its end (Ctrl-C from the command line). A log
    that stops growing for `idle_after` seconds marks the run idle, so it is not shown training for days."""
    reader = Reader(run)
    with open(path, errors="replace") as f:

        def chunks():
            last = clock()
            while True:
                text = f.read()
                if text:
                    last = clock()
                    yield text
                elif stop():
                    return
                else:
                    if clock() - last > idle_after and run.phase != training.PHASES["idle"]:
                        run.idle()
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
        return train(cmd, args.name or default_name(cmd), args.port, args.epochs)
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


def default_name(cmd: list[str]) -> str:
    """A run's name when none is given: the module after -m, else the script, else the program."""
    if "-m" in cmd[:-1]:
        return cmd[cmd.index("-m") + 1]
    script = next((a for a in cmd[1:] if a.endswith(".py")), None)
    return os.path.basename(script or cmd[0])


def _split(argv: list[str]):
    """(our options, "--", the trainer's command)."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], "--", argv[i + 1 :]
    return argv, "", []
