"""Safe subprocess execution shared by all domain modules.

Every external program is started from an argument list (never through a shell), in its
own process group, with stdin closed and a timeout. On timeout the whole group is killed so
that helper processes cannot keep pipes open or survive the call.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


class ProcessTimeout(Exception):
    """The program did not finish in time; its process group was killed."""

    def __init__(self, args: Sequence[str], timeout: float, output: str) -> None:
        super().__init__(f"{args[0]} did not finish within {timeout:g}s")
        self.timeout = timeout
        self.output = output


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


def _kill_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run(
    args: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> Completed:
    """Run `args` and capture text output. Raises ProcessTimeout or OSError."""
    proc = subprocess.Popen(  # noqa: S603 - argument list, no shell
        list(args),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        env=dict(env) if env is not None else None,
        cwd=cwd,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - pipes held by an escaped process
            stdout, stderr = "", ""
        raise ProcessTimeout(args, timeout, (stdout or "") + (stderr or "")) from None
    except BaseException:
        _kill_group(proc)
        proc.wait()
        raise
    # A finished leader may leave helpers behind in its group; do not let them linger.
    _kill_group(proc)
    return Completed(proc.returncode, stdout or "", stderr or "")
