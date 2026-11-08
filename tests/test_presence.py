"""Detecting a project open in Kdenlive (#62).

Kdenlive overwrites a project file when it saves and does not notice external changes, so the
server refuses to replace a project that a running Kdenlive shows. Signals:

- process command line (`kdenlive project.kdenlive`);
- autosave lock of unsaved changes (stalefiles);
- **window title over the session D-Bus**: Kdenlive's main window exports the read-only Qt
  property `windowTitle` ("<project> [*]/ <profile>"), which names the project even when it
  was opened from the *Open* dialog and has no unsaved changes (verified in the GUI lab).

busctl is replaced by a stub here; tests never query the real session bus.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mcp_kdenlive import presence, writer
from mcp_kdenlive.errors import ProjectError

FIX = Path(__file__).parent / "fixtures"
LISTING = [
    {"name": ":1.42", "pid": 4242, "process": "kdenlive"},
    {"name": "org.kde.kdenlive-4242", "pid": 4242, "process": "kdenlive"},
    {"name": "org.freedesktop.Notifications", "pid": 10, "process": "x"},
]


@pytest.fixture
def busctl(make_stub, tmp_path, monkeypatch):
    """A busctl stub answering `list` and `get-property` from files; records calls."""
    state = tmp_path / "bus"
    state.mkdir()
    (state / "list.json").write_text(json.dumps(LISTING))
    (state / "windowTitle").write_text(json.dumps({"type": "s", "data": "p62 [*]/ HD 720p 29.97 fps"}))
    (state / "windowModified").write_text(json.dumps({"type": "b", "data": False}))
    make_stub(
        "busctl",
        f'echo "$@" >> "{state}/calls"\n'
        'for last; do :; done\n'
        f'case "$*" in *" list"*) cat "{state}/list.json" ;; *get-property*) cat "{state}/$last" ;;'
        " *) exit 1 ;; esac",
    )
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    return state


def test_project_name_from_window_title():
    assert presence.project_name("p62 [*]/ HD 720p 29.97 fps") == "p62"
    assert presence.project_name("Mon montage é / x [*]/ 4K UHD 2160p 25 fps") == "Mon montage é / x"
    assert presence.project_name("Untitled [*]/ HD 1080p 25 fps") is None
    assert presence.project_name("") is None


def test_windows_are_read_from_the_session_bus(busctl):
    found = presence.windows()
    assert found == [
        presence.Window(service="org.kde.kdenlive-4242", pid=4242, project="p62", modified=False)
    ]
    calls = (busctl / "calls").read_text()
    # read-only: listing names and reading two properties, nothing else
    assert all(
        line.split()[2] in {"list", "get-property"} for line in calls.splitlines()
    ), calls
    assert "org.qtproject.Qt.QWidget windowTitle" in calls


def test_no_busctl_means_unknown(make_stub, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    (tmp_path / "bin").mkdir(exist_ok=True)
    assert presence.windows() is None


def test_bus_errors_mean_unknown(busctl):
    (busctl / "list.json").write_text("not json")
    assert presence.windows() is None


def test_a_project_shown_by_kdenlive_blocks_overwrite(busctl, tmp_path):
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    dest = tmp_path / "p" / "p62.kdenlive"
    shutil.copy(tmp_path / "p" / "kd26-ntsc.kdenlive", dest)
    usage = writer.kdenlive_usage(dest)
    assert usage.open is True
    assert "p62" in usage.evidence[0] and "window" in usage.evidence[0]
    with pytest.raises(ProjectError) as err:
        writer.save(
            writer.open_for_edit(str(tmp_path / "p" / "kd26-ntsc.kdenlive")), str(dest), overwrite=True
        )
    assert err.value.code == "open_in_kdenlive"


def test_another_project_in_kdenlive_does_not_block(busctl, tmp_path):
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    dest = tmp_path / "p" / "other.kdenlive"
    shutil.copy(tmp_path / "p" / "kd26-ntsc.kdenlive", dest)
    usage = writer.kdenlive_usage(dest)
    assert usage.open is False
    result = writer.save(
        writer.open_for_edit(str(tmp_path / "p" / "kd26-ntsc.kdenlive")), str(dest), overwrite=True
    )
    assert not any("Kdenlive is running" in w for w in result.warnings)


def test_running_kdenlive_that_cannot_be_checked_warns(make_stub, tmp_path, monkeypatch):
    """Kdenlive runs but the bus cannot be read: the write goes through with a warning."""
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    (tmp_path / "bin").mkdir(exist_ok=True)
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    dest = tmp_path / "p" / "copy.kdenlive"
    shutil.copy(tmp_path / "p" / "kd26-ntsc.kdenlive", dest)
    proc = subprocess.Popen(
        ["kdenlive", "-c", "import time; time.sleep(30)"], executable=sys.executable
    )
    try:
        time.sleep(0.2)
        assert writer.kdenlive_usage(dest).open is None
        result = writer.save(
            writer.open_for_edit(str(tmp_path / "p" / "kd26-ntsc.kdenlive")),
            str(dest),
            overwrite=True,
        )
    finally:
        proc.kill()
        proc.wait()
    assert any("Kdenlive is running" in w and "close" in w for w in result.warnings)


def test_no_kdenlive_running_and_no_bus_answer_is_silent(busctl, tmp_path):
    (busctl / "list.json").write_text(json.dumps(LISTING[2:]))
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    dest = tmp_path / "p" / "copy.kdenlive"
    shutil.copy(tmp_path / "p" / "kd26-ntsc.kdenlive", dest)
    result = writer.save(
        writer.open_for_edit(str(tmp_path / "p" / "kd26-ntsc.kdenlive")), str(dest), overwrite=True
    )
    assert result.warnings == [] or not any("Kdenlive" in w for w in result.warnings)
