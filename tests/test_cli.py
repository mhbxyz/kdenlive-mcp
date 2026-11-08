from __future__ import annotations

import json
import os
import subprocess
import sys

from mcp_kdenlive import __version__


def _run(args, env):
    return subprocess.run([sys.executable, "-m", "mcp_kdenlive", *args], capture_output=True,
                          text=True, timeout=60, env=dict(os.environ, **env), check=False)


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
