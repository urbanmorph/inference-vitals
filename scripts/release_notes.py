"""A release's notes: its section of CHANGELOG.md, without the heading. Used by .github/workflows/release.yml.

python scripts/release_notes.py 0.2.1 > notes.md     exits 1 if CHANGELOG.md has no notes for 0.2.1
"""

import pathlib
import re
import sys


def notes(changelog: str, version: str) -> str:
    """The text under "## <version>" (anything after the version on that line is allowed), up to the next "## "."""
    m = re.search(rf"^## {re.escape(version)}\b[^\n]*\n(.*?)(?=^## |\Z)", changelog, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() + "\n" if m else ""


if __name__ == "__main__":
    text = notes(pathlib.Path("CHANGELOG.md").read_text(), sys.argv[1].removeprefix("v"))
    if not text.strip():
        sys.exit(f"CHANGELOG.md has no notes for {sys.argv[1]}")
    sys.stdout.write(text)
