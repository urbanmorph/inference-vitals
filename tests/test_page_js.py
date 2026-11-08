"""Runs the page's JavaScript tests (tests/js) with Node, so `uv run pytest` covers the page too."""

import json
import pathlib
import shutil
import subprocess

import pytest

NODE = shutil.which("node") or next(
    (
        str(p)
        for p in (pathlib.Path("/opt/homebrew/bin/node"), pathlib.Path("/usr/local/bin/node"))
        if p.exists()
    ),
    None,
)


@pytest.mark.skipif(NODE is None, reason="Node is not installed; the page's own tests need it")
def test_the_page_s_javascript():
    tests = sorted(str(p) for p in (pathlib.Path(__file__).parent / "js").glob("*.test.mjs"))
    assert tests, "no JavaScript tests found"
    run = subprocess.run([NODE, "--test", *tests], capture_output=True, text=True, timeout=60, check=False)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]


@pytest.mark.skipif(NODE is None, reason="Node is not installed; the page's own tests need it")
def test_every_tile_kind_the_spec_uses_has_a_renderer_on_the_page():
    from inference_vitals import queries, ui

    page = pathlib.Path(ui.__file__).parent / "page/page.js"
    script = f"console.log(JSON.stringify(Object.keys(require({str(page)!r}).TILES)))"
    run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, check=False)
    assert run.returncode == 0, run.stderr
    kinds = set(json.loads(run.stdout))
    for platform in queries.PLATFORMS:
        spec = ui.spec(platform)
        used = {
            t["kind"] for t in spec["tiles"]["run"] + spec["tiles"]["machine"] + spec["training"]["tiles"]
        }
        assert used <= kinds, used - kinds
