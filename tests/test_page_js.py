"""Runs the page's JavaScript tests (tests/js) with Node, so `uv run pytest` covers the page too."""

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
