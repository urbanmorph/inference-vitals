# Following a fine-tune

[← README](../README.md)

Three ways, easiest first. All publish on `127.0.0.1:8400`, like a model run, so run one at a time; all
publish numbers only. What the page then shows: [dashboard.md](dashboard.md#a-fine-tune).

## 1. Wrap the training command

Its output passes through untouched; the page follows it:

```sh
inference-vitals train --name my-adapter --epochs 2 -- \
  python -m mlx_vlm.train --model-path ./model --data ./data --train-type lora --epochs 2
```

## 2. Follow a log file

For a trainer that is already writing one (Ctrl-C to stop):

```sh
inference-vitals watch-training train.log --name my-adapter
```

Routes 1 and 2 read what the trainer prints, so they work only with trainers whose output they know:

| Trainer | What is read | Tested against |
| --- | --- | --- |
| [mlx-vlm](https://github.com/Blaizzy/mlx-vlm) `mlx_vlm.train` | steps, train and validation loss, learning rate, tokens/s, trained tokens, step time, peak memory, saving | its source (mlx-vlm 0.7) and the output of a live LoRA run on a Mac |
| [mlx-lm](https://github.com/ml-explore/mlx-lm) `mlx_lm.lora` | the same, from its `Iter N: ...` lines | its documented output format |

Lines they do not recognise change nothing, so another trainer's run simply shows no numbers. To add a
trainer, see [CONTRIBUTING.md](../CONTRIBUTING.md).

When the trainer exits, `train` keeps the final numbers up until the dashboard has read them (at most
20 seconds), so even a short run ends as finished on the page.

## 3. Report from your own training loop

Any framework, once it is in your project (`uv add inference-vitals`, or `pip install inference-vitals`):

```python
from inference_vitals import training

run = training.start("my-adapter", total_steps=400, epochs=2)
for step, batch in enumerate(batches, 1):
    # ... a training step ...
    run.step(
        step,
        loss=loss,
        learning_rate=lr,
        tokens_per_second=tps,
        peak_memory_gb=mem,
        epoch=epoch,
        trained_tokens=total_tokens,
    )
    if step % 50 == 0:
        run.validating()
        run.validate(val_loss)
    if step % 100 == 0:
        run.saving()  # while a checkpoint is written
run.finish()  # waits (at most 20 s) until the dashboard has read that it finished
```

Every argument but the step is optional; `step_seconds=` can pass the trainer's own step time. With
`train`, a run is named after the module it runs (`mlx_vlm.train`) unless you give `--name`; two runs with
one name are still told apart by their start time. A loop
that knows which part of a step it is in can also say `run.part("read")`, `"backward"` or `"write"`, and
the band shows it. Each number carries a `run_name` label: the name you give, so keep anything private out
of it. The full list of names is in [metrics.md](metrics.md#a-fine-tune).

## How "Learning?" is decided

From two or more validation passes: **Learning** while the latest validation loss is below every earlier
one; **Watch** when it is not; **Memorising** when it is more than 2% above its best while the training
loss has kept falling since that best.
