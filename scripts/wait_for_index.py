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

FIRST, CAP = 10, 120


class Clock(Protocol):
    def time(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


def wait(probe: Callable[[], bool], max_wait: float, clock: Clock | None = None) -> bool:
    clock = clock or time
    start = clock.time()
    interval = FIRST
    while True:
        if probe():
            print(f"visible on the index after {clock.time() - start:.0f} s", flush=True)
            return True
        remaining = max_wait - (clock.time() - start)
        if remaining <= 0:
            print(f"not visible on the index after {clock.time() - start:.0f} s", flush=True)
            return False
        print(f"not visible yet; next try in {min(interval, remaining):.0f} s", flush=True)
        clock.sleep(min(interval, remaining))
        interval = min(interval * 2, CAP)


def download(pip: str, index: str, dest: str, requirement: str) -> bool:
    done = subprocess.run(  # an argument list, no shell
        [pip, "download", "--no-deps", "--only-binary", ":all:", "--index-url", index,
         "-d", dest, requirement],
        check=False,
    )  # fmt: skip
    return done.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--pip", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--requirement", required=True)
    parser.add_argument("--max-wait", type=float, default=900)
    opts = parser.parse_args(argv)
    found = wait(lambda: download(opts.pip, opts.index, opts.dest, opts.requirement), opts.max_wait)
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
