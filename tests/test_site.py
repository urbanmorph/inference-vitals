"""The website (site/, served at vitals.urbanmorph.com by GitHub Pages) stays true to the repo: its version
is the package's, every file it points to exists, every docs link names a real doc and heading, and search
engines and agents are told where it lives."""

import json
import pathlib
import re
import tomllib

from test_docs import _anchor

ROOT = pathlib.Path(__file__).parents[1]
SITE = ROOT / "site"
URL = "https://vitals.urbanmorph.com/"
REPO = "https://github.com/urbanmorph/inference-vitals/blob/main/"
PAGE = (SITE / "index.html").read_text()
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def _jsonld():
    return [
        json.loads(m)
        for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', PAGE, re.DOTALL)
    ]


def test_the_page_names_itself_for_search():
    title = re.search(r"<title>(.*?)</title>", PAGE).group(1)
    assert "inference-vitals" in title and len(title) <= 70
    desc = re.search(r'<meta name="description" content="([^"]+)"', PAGE).group(1)
    assert 110 <= len(desc) <= 160, len(desc)
    assert f'<link rel="canonical" href="{URL}">' in PAGE
    assert f'<meta property="og:image" content="{URL}og.png">' in PAGE
    assert (SITE / "og.png").exists()


def test_its_version_is_the_packages():
    app = next(d for d in _jsonld() if d.get("@type") == "SoftwareApplication")
    assert app["softwareVersion"] == VERSION
    assert f"v{VERSION}" in PAGE


def test_the_faq_on_the_page_and_in_its_structured_data_agree():
    faq = next(d for d in _jsonld() if d.get("@type") == "FAQPage")
    for q in faq["mainEntity"]:
        assert q["name"] in PAGE, q["name"]


def test_every_file_it_loads_or_links_is_in_the_site():
    for ref in re.findall(r'(?:src|href|srcset)="([^"#:]+)"', PAGE):
        assert (SITE / ref.split(" ")[0].lstrip("/")).exists(), ref


def test_every_link_into_the_repo_names_a_real_file_and_heading():
    for target in re.findall(rf'href="{re.escape(REPO)}([^"]+)"', PAGE):
        path, _, anchor = target.partition("#")
        file = ROOT / path
        assert file.exists(), target
        if anchor:
            headings = {_anchor(h) for h in re.findall(r"^#+ (.+)$", file.read_text(), re.MULTILINE)}
            assert anchor in headings, target


def test_github_pages_serves_it_at_its_own_address():
    assert (SITE / "CNAME").read_text().strip() == "vitals.urbanmorph.com"
    assert f"Sitemap: {URL}sitemap.xml" in (SITE / "robots.txt").read_text()
    assert f"<loc>{URL}</loc>" in (SITE / "sitemap.xml").read_text()
    llms = (SITE / "llms.txt").read_text()
    assert llms.startswith("# inference-vitals\n\n> ")
    assert all(link.startswith("https://") for link in re.findall(r"\]\(([^)]+)\)", llms))
