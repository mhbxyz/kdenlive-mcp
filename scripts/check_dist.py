"""Inspect built distributions in a directory (default: dist/) and fail on packaging mistakes.

Checks: exactly one wheel and one sdist for the same version, expected metadata, required
files present, and no accidental content (research lab, VCS/CI files, caches, secrets).
Usage: python scripts/check_dist.py [DIST_DIR]
"""

from __future__ import annotations

import email.parser
import re
import sys
import tarfile
import zipfile
from pathlib import Path

NAME = "mcp-kdenlive"
SERVER_NAME = "io.github.mhbxyz/kdenlive-mcp"
FORBIDDEN = re.compile(
    r"^research/|(^|/)(\.github|\.git|\.venv|__pycache__|\.pytest_cache|dist|build)(/|$)"
    r"|\.pyc$|(^|/)\.env|\.pem$|\.key$|(^|/)id_rsa|\.kdenlive$"
)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def check_metadata(text: str, origin: str) -> str:
    meta = email.parser.Parser().parsestr(text)
    if meta["Name"] != NAME:
        fail(f"{origin}: Name is {meta['Name']!r}, expected {NAME!r}")
    if meta["Requires-Python"] != ">=3.11":
        fail(f"{origin}: Requires-Python is {meta['Requires-Python']!r}")
    if meta["License-Expression"] != "MIT":
        fail(f"{origin}: License-Expression is {meta['License-Expression']!r}")
    body = meta.get_payload()
    if f"mcp-name: {SERVER_NAME}" not in body:
        fail(f"{origin}: long description lacks the MCP Registry marker 'mcp-name: {SERVER_NAME}'")
    return meta["Version"]


def main() -> None:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        fail(f"expected one wheel and one sdist in {dist}, found {wheels + sdists}")

    with zipfile.ZipFile(wheels[0]) as whl:
        names = whl.namelist()
        for bad in (n for n in names if FORBIDDEN.search(n)):
            fail(f"wheel contains unexpected file {bad}")
        for required in (
            "mcp_kdenlive/__init__.py",
            "mcp_kdenlive/server.py",
            "mcp_kdenlive/py.typed",
        ):
            if required not in names:
                fail(f"wheel lacks {required}")
        if any(n.startswith("tests/") for n in names):
            fail("wheel contains tests")
        meta_name = next(n for n in names if n.endswith(".dist-info/METADATA"))
        wheel_version = check_metadata(whl.read(meta_name).decode(), "wheel")
        if not any(n.endswith(".dist-info/licenses/LICENSE") for n in names):
            fail("wheel lacks LICENSE")
        entry = next(n for n in names if n.endswith(".dist-info/entry_points.txt"))
        if "kdenlive-mcp = mcp_kdenlive.cli:main" not in whl.read(entry).decode():
            fail("console script kdenlive-mcp missing")

    with tarfile.open(sdists[0]) as sd:
        members = [m.name.split("/", 1)[1] for m in sd.getmembers() if "/" in m.name]
        for bad in (n for n in members if FORBIDDEN.search(n)):
            fail(f"sdist contains unexpected file {bad}")
        for required in ("pyproject.toml", "README.md", "LICENSE", "src/mcp_kdenlive/__init__.py"):
            if required not in members:
                fail(f"sdist lacks {required}")
        pkg_info = next(
            m for m in sd.getmembers() if m.name.count("/") == 1 and m.name.endswith("/PKG-INFO")
        )
        sdist_version = check_metadata(sd.extractfile(pkg_info).read().decode(), "sdist")  # type: ignore[union-attr]

    if wheel_version != sdist_version:
        fail(f"wheel version {wheel_version} != sdist version {sdist_version}")
    print(f"OK: {wheels[0].name} and {sdists[0].name}")
    print(f"    version {wheel_version}, {len(names)} files in the wheel")


if __name__ == "__main__":
    main()
