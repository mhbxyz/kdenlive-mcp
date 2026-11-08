"""#82: the release waits for TestPyPI to list a new version, with backoff, then fails clearly."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wait_for_index.py"


def load():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("wait_for_index", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_waits_with_backoff_until_the_version_appears(capsys):
    w, clock = load(), Clock()
    answers = iter([False, False, False, True])
    assert w.wait(lambda: next(answers), max_wait=900, clock=clock) is True
    assert clock.sleeps == [10, 20, 40]  # backoff
    assert "visible on the index after 70 s" in capsys.readouterr().out


def test_gives_up_after_the_deadline_and_says_how_long(capsys):
    w, clock = load(), Clock()
    assert w.wait(lambda: False, max_wait=900, clock=clock) is False
    assert 880 <= clock.now <= 900  # never sleeps past the deadline
    assert max(clock.sleeps) <= 120  # capped interval
    assert "not visible on the index after 900 s" in capsys.readouterr().out


def test_release_workflow_uses_it_and_allows_the_time():
    text = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
    ).read_text()
    job = text.split("verify-testpypi:", 1)[1].split("\n  pypi:", 1)[0]
    assert "scripts/wait_for_index.py" in job and "--max-wait 900" in job
    minutes = int(job.split("timeout-minutes:", 1)[1].split()[0])
    assert minutes >= 25
    assert "sha256sum -c" in job  # what is verified does not change


@pytest.mark.parametrize("ok", [True, False])
def test_main_exit_code(monkeypatch, ok, tmp_path):
    w = load()
    monkeypatch.setattr(w, "download", lambda *a: ok)
    monkeypatch.setattr(w, "wait", lambda probe, max_wait, clock=None, what="": probe())
    code = w.main(
        [
            "--pip",
            "pip",
            "--index",
            "https://test.pypi.org/simple/",
            "--dest",
            str(tmp_path),
            "--requirement",
            "mcp-kdenlive==0.3.0",
            "--max-wait",
            "900",
        ]
    )
    assert code == (0 if ok else 1)
