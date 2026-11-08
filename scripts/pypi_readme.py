"""The README for PyPI: relative links and images become GitHub links pinned to the released tag, since PyPI
shows the README on its own, away from the repository's files. Used by .github/workflows/release.yml.

    python scripts/pypi_readme.py v0.2.0     rewrites README.md in place (in the release build only)
"""

import pathlib
import re
import sys

REPO = "https://github.com/urbanmorph/inference-vitals"
IMAGE = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
RELATIVE = re.compile(r'(\]\(|srcset="|src=")(?!https?://|#|mailto:)([^)"\s]+)')


def absolute(text: str, tag: str) -> str:
    def link(m):
        path = m.group(2)
        kind = "raw" if path.split("#")[0].lower().endswith(IMAGE) else "blob"
        return f"{m.group(1)}{REPO}/{kind}/{tag}/{path}"

    return RELATIVE.sub(link, text)


if __name__ == "__main__":
    readme = pathlib.Path("README.md")
    readme.write_text(absolute(readme.read_text(), sys.argv[1]))
