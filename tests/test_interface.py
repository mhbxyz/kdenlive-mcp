"""Public interface guard (docs/versioning.md): tools, parameters, annotations, error codes.

If this fails, the public surface changed: update tests/interface_snapshot.json *and* the
changelog in the same change, and pick the version bump the policy requires.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client

from mcp_kdenlive.server import build_server

ROOT = Path(__file__).resolve().parents[1]


def output_fields(schema: dict) -> list[str]:
    """Every output field name, nested ones as 'Model.field' (from $defs)."""
    names = [str(k) for k in schema.get("properties", {})]
    for model, definition in schema.get("$defs", {}).items():
        names += [f"{model}.{k}" for k in definition.get("properties", {})]
    return sorted(names)


def describe(tool) -> dict:  # type: ignore[no-untyped-def,type-arg]
    a = tool.annotations
    return {
        "params": sorted(tool.input_schema.get("properties", {})),
        "required": sorted(tool.input_schema.get("required", [])),
        "read_only": bool(a and a.read_only_hint),
        "destructive": bool(a and a.destructive_hint),
        "idempotent": bool(a and a.idempotent_hint),
        "output": output_fields(tool.output_schema or {}),
    }


@pytest.mark.anyio
async def test_tools_match_snapshot():
    async with Client(build_server()) as client:
        tools = (await client.list_tools()).tools
    current = {t.name: describe(t) for t in tools}
    expected = json.loads((ROOT / "tests" / "interface_snapshot.json").read_text())
    assert current == expected


def test_error_codes_are_documented():
    raised = set()
    for path in (ROOT / "src" / "mcp_kdenlive").glob("*.py"):
        raised |= set(re.findall(r'ProjectError\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
    doc = (ROOT / "docs" / "versioning.md").read_text(encoding="utf-8")
    section = doc.split("### Error codes", 1)[1].split("\n## ", 1)[0]
    documented = set(re.findall(r"`([a-z_]+)`", section))
    assert raised == documented


def test_every_error_code_is_exercised_by_a_test():
    """Each documented code is expected by at least one test (#63)."""
    raised = set()
    for path in (ROOT / "src" / "mcp_kdenlive").glob("*.py"):
        raised |= set(re.findall(r'ProjectError\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
    tests = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "tests").rglob("*.py"))
    untested = sorted(code for code in raised if f'"{code}"' not in tests)
    assert untested == []


def _run_check(tmp_path, pyproject_version: str, *args: str, changelog: str = "", server=None):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "check_version.py", root / "scripts")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{pyproject_version}"\n'
    )
    (root / "CHANGELOG.md").write_text(changelog)
    if server is not None:
        (root / "server.json").write_text(json.dumps(server))
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "check_version.py"), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_version_check_accepts_a_consistent_release(tmp_path):
    server = {"version": "0.1.0", "packages": [{"identifier": "mcp-kdenlive", "version": "0.1.0"}]}
    done = _run_check(
        tmp_path, "0.1.0", "v0.1.0", changelog="## [0.1.0] - 2026-11-08\n", server=server
    )
    assert done.returncode == 0, done.stdout


@pytest.mark.parametrize(
    ("version", "tag", "changelog", "server", "message"),
    [
        ("0.1.0", "v0.1.1", "## [0.1.0]", None, "does not match"),
        ("0.1.0.dev0", "v0.1.0.dev0", "## [0.1.0.dev0]", None, "development version"),
        ("0.1.0", "v0.1.0", "## [Unreleased]", None, "no '## [0.1.0]'"),
        (
            "0.1.0",
            "v0.1.0",
            "## [0.1.0]",
            {"version": "0.0.9", "packages": []},
            "server.json version",
        ),
        (
            "0.1.0",
            "v0.1.0",
            "## [0.1.0]",
            {"version": "0.1.0", "packages": [{"identifier": "p", "version": "0.0.9"}]},
            "package p",
        ),
        ("one.two", None, "", None, "PEP 440"),
    ],
)
def test_version_check_rejects_inconsistencies(tmp_path, version, tag, changelog, server, message):
    done = _run_check(
        tmp_path, version, *([tag] if tag else []), changelog=changelog, server=server
    )
    assert done.returncode == 1 and message in done.stdout


def test_repository_versions_are_consistent():
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_version.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout
