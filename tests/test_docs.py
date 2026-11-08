"""The docs stay true to the code: links resolve, the metric list matches what is exported (both ways),
and every command they show exists."""

import pathlib
import re

import pytest
from test_monitor import _known_metric_names

from inference_vitals import monitor

ROOT = pathlib.Path(__file__).parents[1]
DOCS = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "CONTRIBUTING.md", ROOT / "CLAUDE.md", ROOT / "llms.txt",
        *sorted((ROOT / "docs").glob("*.md"))]  # fmt: skip
LINK = re.compile(r"\]\(([^)\s]+)\)|srcset=\"([^\"]+)\"|src=\"([^\"]+)\"")
HISTOGRAM = re.compile(r"_(bucket|sum|count)$")


def _anchor(heading: str) -> str:  # as GitHub makes them
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_every_link_to_a_file_or_heading_in_the_repo_resolves(doc):
    for groups in LINK.findall(doc.read_text()):
        target = next(g for g in groups if g)
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        file = (doc.parent / path).resolve() if path else doc
        assert file.exists(), f"{doc.name}: {target}"
        if anchor and file.suffix == ".md":
            headings = {_anchor(h) for h in re.findall(r"^#+ (.+)$", file.read_text(), re.MULTILINE)}
            assert anchor in headings, f"{doc.name}: {target}"


def test_the_metric_list_and_the_code_agree_both_ways():
    known = _known_metric_names()
    exported = {
        HISTOGRAM.sub("", n) for n in known[monitor.MAC] | known[monitor.LINUX] if not n.startswith("macmon_")
    }
    listed = set(re.findall(r"`((?:vitals_|vllm:)[a-z0-9_:]+)`", (ROOT / "docs" / "metrics.md").read_text()))
    assert exported - listed == set(), "exported but not in docs/metrics.md"
    assert listed - exported == set(), "in docs/metrics.md but not exported"


def test_every_command_the_docs_show_exists():
    commands = {"start", "stop", "status", "proxy", "train", "watch-training"}
    for doc in DOCS:
        for cmd in re.findall(r"run inference-vitals ([a-z-]+)", doc.read_text()):
            assert cmd in commands, f"{doc.name}: inference-vitals {cmd}"
    monitor_src = (ROOT / "src" / "inference_vitals" / "monitor.py").read_text()
    for cmd in commands - {"start", "stop", "status"}:
        assert f'"{cmd}"' in monitor_src, cmd  # the command line knows it
