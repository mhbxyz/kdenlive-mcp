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

- **Tests:** unit, MCP protocol and end-to-end tests (the real server driven over stdio by an MCP client, renders checked objectively: streams, duration, frame colors, audio levels) on Python 3.11–3.14, with line and branch coverage and a floor.
- **Interface guard:** every tool's parameters, annotations and output fields are snapshotted; every error code is documented and exercised by a test.
- **Artifacts:** the wheel and sdist were built once, checked, installed in clean environments and smoke-tested. The same files went to TestPyPI, were installed from there and tested, then were promoted.
- **Real tools:** integration and end-to-end tests against Ubuntu 24.04's Kdenlive 23.08 / MLT 7.22 / FFmpeg 6.1 (renders through melt there).

**Local, with Kdenlive 26.08.2 (recorded in the pull requests and `docs/compatibility.md`):**

- **Round trip:** projects generated or edited by every new tool open without dialogs and survive Kdenlive's own save unchanged, including crossfades, effects, guides, markers and relinked media (`research/lab/verify_roundtrip.py`).
- **Rendering:** `kdenlive --render` renders them (end-to-end tests).
- **Real client:** a multi-step in-place edit by Claude Code (Opus 5.5) over MCP, before and after the interface review.

**Not verified automatically: manual Kdenlive GUI checks are still pending.**
Interactive editing, undo/redo and GUI rendering of server-written projects have not all been
checked by a person. The checklists are linked from #67; treat them as known limitations.
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
