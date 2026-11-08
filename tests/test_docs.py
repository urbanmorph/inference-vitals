"""The docs stay true to the code: links resolve, the metric list matches what is exported (both ways),
and every command they show exists."""

import pathlib
import re

import pytest
from helpers import known_metric_names

from inference_vitals import queries

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
    known = known_metric_names()
    exported = {
        HISTOGRAM.sub("", n) for n in known[queries.MAC] | known[queries.LINUX] if not n.startswith("macmon_")
    }
    listed = set(
        re.findall(r"`((?:vitals_|vitals:|vllm:)[a-z0-9_:]+)`", (ROOT / "docs" / "metrics.md").read_text())
    )
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


def test_the_pypi_readme_points_every_relative_link_at_the_released_tag():
    import importlib.util

    spec = importlib.util.spec_from_file_location("pypi_readme", ROOT / "scripts/pypi_readme.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = "![shot](docs/screenshots/a.png) [install](docs/install.md#mac) [web](https://x.org) [top](#why)"
    out = mod.absolute(text, "v0.2.0")
    base = "https://github.com/urbanmorph/inference-vitals"
    assert f"![shot]({base}/raw/v0.2.0/docs/screenshots/a.png)" in out  # images: the file itself
    assert f"[install]({base}/blob/v0.2.0/docs/install.md#mac)" in out
    assert "[web](https://x.org)" in out and "[top](#why)" in out  # left alone
    readme = mod.absolute((ROOT / "README.md").read_text(), "v0.2.0")
    assert not re.findall(r"\]\((?!https?://|#|mailto:)", readme)  # nothing relative left


def test_the_release_workflow_matches_the_pypi_trusted_publisher():
    flow = (ROOT / ".github/workflows/release.yml").read_text()
    assert 'tags: ["v*"]' in flow
    assert (
        "name: pypi" in flow and "id-token: write" in flow
    )  # PyPI's pending publisher: release.yml, env pypi
    assert "pypa/gh-action-pypi-publish" in flow and "password" not in flow  # no stored token
    assert "uv run pytest" in flow and 'test "v$version" = "$GITHUB_REF_NAME"' in flow


def test_the_readme_badges_point_at_this_package_and_its_release_workflow():
    top = (ROOT / "README.md").read_text().split("**One screen", 1)[0]
    assert "img.shields.io/pypi/v/inference-vitals" in top and "pypi.org/project/inference-vitals" in top
    assert "img.shields.io/pypi/pyversions/inference-vitals" in top
    assert "actions/workflows/release.yml/badge.svg" in top
    assert (ROOT / ".github/workflows/release.yml").exists()
    assert "license-MIT" in top and (ROOT / "LICENSE").read_text().startswith("MIT")


def test_users_install_from_pypi_and_never_need_a_clone():
    for doc in ["README.md", "AGENTS.md", "docs/install.md", "docs/connect.md", "docs/training.md"]:
        text = (ROOT / doc).read_text()
        source = re.search(r"## From source\n.*?(?=\n## )", text, re.S)  # install.md: running from a clone
        text = text.replace(source.group(0), "") if source else text
        assert "uv run inference-vitals" not in text, doc  # an installed tool runs as `inference-vitals`
        assert "git clone" not in text, doc
    assert "uv tool install inference-vitals" in (ROOT / "README.md").read_text()
    install = (ROOT / "docs/install.md").read_text()
    assert "uv tool upgrade inference-vitals" in install and "uv tool uninstall inference-vitals" in install
    assert "uv add inference-vitals" in (ROOT / "docs/connect.md").read_text()  # the library, in one's project
