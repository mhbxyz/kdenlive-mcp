"""Static guards on GitHub workflows (supply chain #28, release #29, governance #31)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_to_commit_shas(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.search(r"uses:\s*([^\s#]+)", line)
        if match and not match.group(1).startswith("./"):
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", match.group(1)), line.strip()
            assert "#" in line, f"pin without a version comment: {line.strip()}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_privileged_pr_triggers_and_explicit_permissions(path):
    text = path.read_text(encoding="utf-8")
    assert "pull_request_target" not in text
    assert re.search(r"^permissions:", text, re.M), "top-level permissions must be explicit"


def test_release_workflow_matches_trusted_publishers():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    trigger = text.split("jobs:", 1)[0]
    assert 'tags: ["v*"]' in trigger and "pull_request" not in trigger and "branches" not in trigger
    # The PyPI/TestPyPI pending publishers were registered for exactly these names (#29).
    assert "name: testpypi" in text and "name: pypi" in text
    assert re.search(r"^permissions: \{\}$", text, re.M)
    assert "check_version.py" in text and "check_dist.py" in text and "smoke_installed.py" in text
    # MCP Registry: final tags only, after PyPI, publisher verified with Sigstore before use.
    registry = text.split("  mcp-registry:", 1)[1].split("\n  github-release:", 1)[0]
    assert "needs: [build, pypi]" in registry and "prerelease == 'false'" in registry
    assert registry.index("cosign verify-blob") < registry.index("mcp-publisher publish")
    assert "login github-oidc" in registry and "releases/latest" not in registry
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert "--extra-index-url" not in code  # never resolve dependencies from the test index


def test_release_notes_extract_the_version_section(tmp_path):
    root = tmp_path / "r"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "release_notes.py").write_text(
        (ROOT / "scripts" / "release_notes.py").read_text(encoding="utf-8")
    )
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [0.2.0] - 2026-12-01\n\n"
        "### Added\n\n- thing\n\n## [0.1.0] - 2026-11-08\n\n- old\n"
    )
    done = subprocess.run(
        [sys.executable, str(root / "scripts" / "release_notes.py"), "0.2.0"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "- thing" in done.stdout and "- old" not in done.stdout
    assert "manual Kdenlive GUI checks are still pending" in done.stdout
    missing = subprocess.run(
        [sys.executable, str(root / "scripts" / "release_notes.py"), "9.9.9"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0
