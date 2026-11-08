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
CAT = shutil.which("cat")  # resolved before tests replace PATH
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
    (state / "windowTitle").write_text(
        json.dumps({"type": "s", "data": "p62 [*]/ HD 720p 29.97 fps"})
    )
    (state / "windowModified").write_text(json.dumps({"type": "b", "data": False}))
    make_stub(
        "busctl",
        f'echo "$@" >> "{state}/calls"\n'
        "for last; do :; done\n"
        f'case "$*" in *" list"*) {CAT} "{state}/list.json" ;;'
        f' *get-property*) {CAT} "{state}/$last" ;; *) exit 1 ;; esac',
    )
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    # Only the stubbed bus counts here, not a Kdenlive that may run on the test machine.
    monkeypatch.setattr(presence, "kdenlive_processes", dict)
    return state


def _fake_kdenlive(pid: int):  # type: ignore[no-untyped-def]
    return lambda: {pid: []}


def test_project_name_from_window_title():
    assert presence.project_name("p62 [*]/ HD 720p 29.97 fps") == "p62"
    assert presence.project_name("p62 [*]/ DV/DVD PAL") == "p62"  # profile names may hold "/"
    assert presence.project_name("Mon / montage é [*]/ 4K UHD 2160p 25 fps") == "Mon / montage é"
    # an untitled document is reported as such: a file named Untitled.kdenlive still matches
    assert presence.project_name("Untitled [*]/ HD 1080p 25 fps") == "Untitled"
    assert presence.project_name("") is None
    assert presence.project_name("Some other title format") is None


def test_windows_are_read_from_the_session_bus(busctl):
    found = presence.windows()
    assert found == [
        presence.Window(service="org.kde.kdenlive-4242", pid=4242, project="p62", modified=False)
    ]
    calls = (busctl / "calls").read_text()
    # read-only: listing names and reading two properties, nothing else
    assert all(line.split()[3] in {"list", "get-property"} for line in calls.splitlines()), calls
    assert "org.qtproject.Qt.QWidget windowTitle" in calls


def test_no_busctl_means_unknown(make_stub, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    (tmp_path / "bin").mkdir(exist_ok=True)
    assert presence.windows() is None


def test_bus_errors_mean_unknown(busctl):
    (busctl / "list.json").write_text("not json")
    assert presence.windows() is None


def test_a_project_shown_by_kdenlive_blocks_overwrite(busctl, tmp_path, monkeypatch):
    monkeypatch.setattr(presence, "kdenlive_processes", _fake_kdenlive(4242))
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    dest = tmp_path / "p" / "p62.kdenlive"
    shutil.copy(tmp_path / "p" / "kd26-ntsc.kdenlive", dest)
    usage = writer.kdenlive_usage(dest)
    assert usage.open is True
    assert "p62" in usage.evidence[0] and "window" in usage.evidence[0]
    with pytest.raises(ProjectError) as err:
        writer.save(
            writer.open_for_edit(str(tmp_path / "p" / "kd26-ntsc.kdenlive")),
            str(dest),
            overwrite=True,
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


def test_no_kdenlive_process_is_silent(busctl, tmp_path):
    (busctl / "list.json").write_text(json.dumps(LISTING[2:]))
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    dest = tmp_path / "p" / "copy.kdenlive"
    shutil.copy(tmp_path / "p" / "kd26-ntsc.kdenlive", dest)
    result = writer.save(
        writer.open_for_edit(str(tmp_path / "p" / "kd26-ntsc.kdenlive")), str(dest), overwrite=True
    )
    assert not any("Kdenlive" in w for w in result.warnings)


def test_every_running_kdenlive_checked_by_pid(busctl, tmp_path, monkeypatch):
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    dest = tmp_path / "p" / "other.kdenlive"
    shutil.copy(tmp_path / "p" / "kd26-ntsc.kdenlive", dest)
    monkeypatch.setattr(presence, "kdenlive_processes", _fake_kdenlive(4242))
    assert writer.kdenlive_usage(dest).open is False  # its window shows p62
    monkeypatch.setattr(presence, "kdenlive_processes", _fake_kdenlive(999))
    assert writer.kdenlive_usage(dest).open is None  # pid 999 has no window on the bus


@pytest.mark.parametrize("title", ["", "Kdenlive", None])
def test_unreadable_titles_are_unchecked(busctl, tmp_path, monkeypatch, title):
    if title is None:
        (busctl / "windowTitle").write_text("{}")
    else:
        (busctl / "windowTitle").write_text(json.dumps({"type": "s", "data": title}))
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    dest = tmp_path / "p" / "p62.kdenlive"
    shutil.copy(tmp_path / "p" / "kd26-ntsc.kdenlive", dest)
    monkeypatch.setattr(presence, "kdenlive_processes", _fake_kdenlive(4242))
    assert writer.kdenlive_usage(dest).open is None


def test_create_project_does_not_replace_a_project_shown_by_kdenlive(
    busctl, stub_melt, tmp_path, monkeypatch
):
    monkeypatch.setattr(presence, "kdenlive_processes", _fake_kdenlive(4242))
    env, _ = stub_melt
    from mcp_kdenlive import create

    shutil.copytree(FIX / "kd26", tmp_path / "p")
    out = tmp_path / "p" / "p62.kdenlive"
    shutil.copy(tmp_path / "p" / "kd26-ntsc.kdenlive", out)
    with pytest.raises(ProjectError) as err:
        create.create_project(
            str(out), str(tmp_path / "p" / "media" / "ntsc.mp4"), "atsc_720p_2997",
            overwrite=True, env=env,
        )  # fmt: skip
    assert err.value.code == "open_in_kdenlive"


def test_kdenlive_render_helpers_are_not_kdenlive_windows(tmp_path):
    helper = subprocess.Popen(
        ["kdenlive_render", "-c", "import time; time.sleep(30)"], executable=sys.executable
    )
    try:
        time.sleep(0.2)
        assert helper.pid not in presence.kdenlive_processes()
    finally:
        helper.kill()
        helper.wait()


def test_command_line_renders_are_not_open_projects():
    """Found by a real-client demo run (#92): `kdenlive --render` (e.g. a render running at the
    same time) never holds a project open for editing, so it must not trigger the warning."""
    render = subprocess.Popen(
        ["kdenlive", "-c", "import time; time.sleep(30)", "--render", "a.kdenlive", "o.mp4"],
        executable=sys.executable,
    )
    gui = subprocess.Popen(
        ["kdenlive", "-c", "import time; time.sleep(30)", "a.kdenlive"], executable=sys.executable
    )
    try:
        time.sleep(0.2)
        found = presence.kdenlive_processes()
        assert render.pid not in found and gui.pid in found
    finally:
        for proc in (render, gui):
            proc.kill()
            proc.wait()


def test_autosave_lock_of_unsaved_changes(tmp_path, monkeypatch):
    import os
    import urllib.parse

    shutil.copytree(FIX / "kd26", tmp_path / "p")
    dest = tmp_path / "p" / "edited.kdenlive"
    shutil.copy(tmp_path / "p" / "kd26-ntsc.kdenlive", dest)
    stale = tmp_path / "data" / "stalefiles" / "kdenlive"
    stale.mkdir(parents=True)
    name = urllib.parse.quote(str(dest.resolve()), safe="")
    (stale / f"kdenlive-{name}.lock").write_text(f"{os.getpid()}\nkdenlive\nhost\n")
    (stale / "kdenlive-other.lock").write_text("1\n")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(presence, "kdenlive_processes", dict)
    monkeypatch.setattr(presence, "windows", lambda env=None: [])
    usage = presence.kdenlive_usage(dest)
    assert usage.open is True and "autosave" in usage.evidence[0]
    (stale / f"kdenlive-{name}.lock").write_text("not a pid\n")
    assert presence.kdenlive_usage(dest).open is False
