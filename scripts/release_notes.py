"""Print the GitHub release notes for VERSION: its CHANGELOG section plus a verification note.

Usage: python scripts/release_notes.py VERSION
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

VERIFICATION = """
## How this release was verified

**Automated (CI, every release):**

- **Tests:** unit and MCP protocol tests on Python 3.11–3.14.
- **Artifacts:** the wheel and sdist were built once, checked, installed in clean environments and smoke-tested. The same files went to TestPyPI, were installed from there and tested, then were promoted.
- **Real tools:** integration tests against Ubuntu 24.04's Kdenlive 23.08 / MLT 7.22 / FFmpeg 6.1, including melt rendering of every fixture.

**Local, with Kdenlive 26.08.2 (recorded in `docs/compatibility.md`):**

- **Round trip:** generated and edited projects open without dialogs and survive Kdenlive's own save unchanged (`research/lab/verify_roundtrip.py`).
- **Rendering:** `kdenlive --render` renders them.

**Not verified automatically: manual Kdenlive GUI checks are still pending.**
The checklists in #8, #10 and #11 (interactive editing, undo/redo, GUI render) have not all been
performed. Treat them as known limitations.
"""


def section(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.S | re.M)
    if not match:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    return match.group(1).strip()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(section(sys.argv[1]) + "\n" + VERIFICATION)
