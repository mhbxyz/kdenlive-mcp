from __future__ import annotations

import json
import os
import subprocess
import sys

from mcp_kdenlive import __version__


def _run(args, env):
    return subprocess.run(
        [sys.executable, "-m", "mcp_kdenlive", *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=dict(os.environ, **env),
        check=False,
    )


def test_version():
    proc = _run(["--version"], {})
    assert proc.returncode == 0
    assert proc.stdout.strip() == f"kdenlive-mcp {__version__}"


def test_diagnose_json_ready(all_tools_stubbed):
    proc = _run(["diagnose", "--json"], all_tools_stubbed)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["ready"] is True


def test_diagnose_not_ready_exit_code(stub_env):
    proc = _run(["diagnose"], stub_env)
    assert proc.returncode == 1
    assert "NOT ready" in proc.stdout
    assert "hint:" in proc.stdout


def test_diagnose_shows_filesystem_scope(all_tools_stubbed, tmp_path):
    proc = _run(["diagnose"], dict(all_tools_stubbed, KDENLIVE_MCP_ALLOWED_ROOTS=str(tmp_path)))
    assert f"files     {tmp_path} (KDENLIVE_MCP_ALLOWED_ROOTS)" in proc.stdout
    proc = _run(["diagnose"], dict(all_tools_stubbed, KDENLIVE_MCP_ALLOWED_ROOTS="unrestricted"))
    assert "files     unrestricted" in proc.stdout


def test_serve_logs_filesystem_scope_on_stderr(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_kdenlive", "serve"],
        input="",
        capture_output=True,
        text=True,
        timeout=60,
        env=dict(os.environ, KDENLIVE_MCP_ALLOWED_ROOTS=str(tmp_path)),
        check=False,
    )
    assert proc.stdout == ""
    assert f"Filesystem access: {tmp_path}" in proc.stderr
