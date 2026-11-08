"""Fail if a runtime dependency has a license outside the allow-list (docs/supply-chain.md).

Run inside the project environment: `uv run python scripts/check_licenses.py`.
Runtime dependencies are read from `uv export --no-dev`; licenses from installed metadata.
"""

from __future__ import annotations

import re
import subprocess
from importlib import metadata

# Permissive or weak (file-level) copyleft licenses compatible with distributing an MIT package
# that depends on them without bundling them.
ALLOWED = ("MIT", "BSD", "Apache", "ISC", "PSF", "Python Software Foundation", "MPL-2.0",
           "Mozilla Public License 2.0", "Unlicense", "0BSD", "Zlib", "HPND", "CNRI-Python")
# Recorded exceptions: package -> reason (reviewed by the maintainer).
EXCEPTIONS: dict[str, str] = {}


def runtime_packages() -> list[tuple[str, bool]]:
    """(name, has_environment_marker) for every runtime dependency."""
    out = subprocess.run(["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes"],
                         capture_output=True, text=True, check=True).stdout
    return [(m.group(1), ";" in line) for line in out.splitlines()
            if (m := re.match(r"^([A-Za-z0-9_.\-]+)==", line))]


def license_of(name: str) -> str:
    meta = metadata.metadata(name)
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or []
                   if c.startswith("License ::")]
    if classifiers:
        return " / ".join(classifiers)
    return (meta.get("License") or "UNKNOWN").splitlines()[0][:80]


def main() -> None:
    problems = []
    for name, has_marker in runtime_packages():
        try:
            lic = license_of(name)
        except metadata.PackageNotFoundError:
            if has_marker:  # e.g. pywin32 (Windows only): not part of a Linux installation
                print(f"--  {name}: skipped (platform-specific, not installed here)")
            else:
                problems.append(f"{name}: not installed in this environment")
            continue
        ok = any(token.lower() in lic.lower() for token in ALLOWED) and "GPL" not in lic.replace("LGPL", "")
        print(f"{'ok ' if ok or name in EXCEPTIONS else 'BAD'} {name}: {lic}")
        if not ok and name not in EXCEPTIONS:
            problems.append(f"{name}: {lic}")
    if problems:
        raise SystemExit("License check failed:\n  " + "\n  ".join(problems))
    print("OK: all runtime dependency licenses are allowed")


if __name__ == "__main__":
    main()
