"""Check that every place carrying the release version agrees (docs/versioning.md).

Usage: python scripts/check_version.py [TAG]
  - without TAG: pyproject version is PEP 440-valid; server.json (if present) matches.
  - with TAG (vX.Y.Z): additionally TAG == "v" + version, the version is not a .dev build,
    and CHANGELOG.md has a "## [X.Y.Z]" section.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PEP440 = re.compile(r"^\d+\.\d+\.\d+((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def main() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    if not PEP440.match(version):
        fail(f"pyproject version {version!r} is not a supported PEP 440 version")
    server_json = ROOT / "server.json"
    if server_json.exists():
        data = json.loads(server_json.read_text(encoding="utf-8"))
        if data.get("version") != version:
            fail(f"server.json version {data.get('version')!r} != {version!r}")
        for package in data.get("packages", []):
            if package.get("version") != version:
                fail(
                    f"server.json package {package.get('identifier')} version "
                    f"{package.get('version')!r} != {version!r}"
                )
    if len(sys.argv) > 1:
        tag = sys.argv[1]
        if tag != f"v{version}":
            fail(f"tag {tag!r} does not match package version {version!r} (expected v{version})")
        if ".dev" in version:
            fail(f"{version} is a development version; it cannot be released")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        if f"## [{version}]" not in changelog:
            fail(f"CHANGELOG.md has no '## [{version}]' section")
    print(f"OK: version {version}" + (f" matches tag {sys.argv[1]}" if len(sys.argv) > 1 else ""))


if __name__ == "__main__":
    main()
