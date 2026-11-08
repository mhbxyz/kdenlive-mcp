"""Wait until a package index serves a release, then leave its wheel in a folder (#82).

TestPyPI can take several minutes to list a new upload. The release workflow polls with
backoff (10 s, 20 s, 40 s… capped at 2 minutes) for up to `--max-wait` seconds, says how long it
waited, and fails clearly instead of letting a later step trip over a missing file.

Usage: python scripts/wait_for_index.py --pip PIP --index URL --dest DIR
                                        --requirement 'name==version' [--max-wait 900]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Protocol

FIRST_DELAY_S, MAX_DELAY_S = 10, 120
ATTEMPT_TIMEOUT_S = 180  # one pip download; counted in the overall wait


class Clock(Protocol):
    def time(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


def wait(
    probe: Callable[[], bool], max_wait: float, clock: Clock | None = None, what: str = ""
) -> bool:
    clock = clock or time
    start = clock.time()
    interval = FIRST_DELAY_S
    while True:
        if probe():
            print(
                f"{what} visible on the index after {clock.time() - start:.0f} s".strip(),
                flush=True,
            )
            return True
        remaining = max_wait - (clock.time() - start)
        if remaining <= 0:
            print(
                f"{what} not visible on the index after {clock.time() - start:.0f} s".strip(),
                flush=True,
            )
            return False
        print(f"not visible yet; next try in {min(interval, remaining):.0f} s", flush=True)
        clock.sleep(min(interval, remaining))
        interval = min(interval * 2, MAX_DELAY_S)


def download(pip: str, index: str, dest: str, requirement: str) -> bool:
    try:
        done = subprocess.run(  # an argument list, no shell
            [pip, "download", "--no-deps", "--only-binary", ":all:", "--index-url", index,
             "--timeout", "60", "-d", dest, requirement],
            check=False,
            timeout=ATTEMPT_TIMEOUT_S,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return False
    return done.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wait until a package index serves a release.")
    parser.add_argument("--pip", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--requirement", required=True)
    parser.add_argument("--max-wait", type=float, default=900)
    opts = parser.parse_args(argv)
    found = wait(
        lambda: download(opts.pip, opts.index, opts.dest, opts.requirement),
        opts.max_wait,
        what=f"{opts.requirement} on {opts.index}:",
    )
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
