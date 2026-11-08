from __future__ import annotations

import os
import shutil
import sys
import time

import pytest

from mcp_kdenlive.process import ProcessTimeout, run


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); treat it as dead.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().split()[2] != "Z"
    except FileNotFoundError:
        return False


def test_timeout_kills_whole_process_group(tmp_path):
    pidfile = tmp_path / "child.pid"
    script = tmp_path / "spawner.sh"
    sleep = shutil.which("sleep")
    script.write_text(f"#!/bin/sh\n{sleep} 30 &\necho $! > {pidfile}\n{sleep} 30\n")
    script.chmod(0o755)
    started = time.monotonic()
    with pytest.raises(ProcessTimeout):
        run([str(script)], timeout=0.5)
    assert time.monotonic() - started < 10
    child = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while _pid_alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(child), "background child survived the timeout"


def test_arguments_are_not_shell_interpreted(tmp_path):
    target = tmp_path / "should-not-exist"
    result = run(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", f"$(touch {target})"], timeout=10
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"$(touch {target})"
    assert not target.exists()


def test_nonzero_exit_and_stderr_are_captured():
    result = run(
        [sys.executable, "-c", "import sys; sys.stderr.write('bad'); sys.exit(3)"], timeout=10
    )
    assert (result.returncode, result.stderr) == (3, "bad")


def test_missing_executable_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        run([str(tmp_path / "nope")], timeout=5)


def test_stdin_is_closed():
    result = run([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], timeout=10)
    assert result.stdout.strip() == "''"
