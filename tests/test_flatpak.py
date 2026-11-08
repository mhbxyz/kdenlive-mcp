"""Kdenlive installed as a Flatpak (#65).

Observed with Flathub's org.kde.kdenlive 26.08.3 (melt 7.43.0, FFmpeg 9.0.2 inside):

- the app is not on PATH; Flatpak exports a launcher in `<installation>/exports/bin`;
- `flatpak run --command=melt|ffprobe|ffmpeg org.kde.kdenlive` runs the bundled tools;
- `filesystems=host` shows the home and most host folders, **but not /tmp** (nor /usr, /etc…):
  a project in /tmp makes `kdenlive --render` exit 0 without writing anything;
- `flatpak run` replaces TMPDIR and XDG_CONFIG_HOME; other variables pass through.

Here `flatpak` is a stub that records its arguments; nothing is installed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mcp_kdenlive import environment, flatpak
from mcp_kdenlive.errors import ProjectError

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def fp(tmp_path, make_stub, stub_env, monkeypatch):
    """A user-installed Flatpak Kdenlive and a recording `flatpak` stub; nothing on PATH."""
    home = tmp_path / "home"
    exports = home / ".local" / "share" / "flatpak" / "exports" / "bin"
    exports.mkdir(parents=True)
    (exports / flatpak.APP).write_text("#!/bin/sh\n")
    record = tmp_path / "flatpak.args"
    stub = make_stub(
        "flatpak",
        f'printf "%s\\n" "$@" > "{record}"; printf "%s\\n" "TMPDIR=$TMPDIR" >> "{record}"\n'
        'case "$*" in *--command=kdenlive*) echo "kdenlive 26.08.3";;'
        ' *--command=melt*) echo "melt 7.43.0";;'
        ' *--command=ffprobe*) echo "ffprobe version 9.0.2 Copyright";;'
        ' *--command=ffmpeg*) echo "ffmpeg version 9.0.2 Copyright";; esac',
    )
    env = dict(stub_env, HOME=str(home), XDG_CACHE_HOME=str(tmp_path / "cache"))
    env.pop("KDENLIVE_MCP_FLATPAK", None)
    monkeypatch.delenv("KDENLIVE_MCP_FLATPAK", raising=False)
    monkeypatch.setattr(flatpak, "FLATPAK", str(stub))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env, record


def test_flatpak_kdenlive_and_its_tools_are_found(fp):
    env, record = fp
    report = environment.diagnose(env)
    by_name = {t.name: t for t in report.tools}
    assert {n: (t.status.value, t.source) for n, t in by_name.items()} == {
        "kdenlive": ("available", "flatpak"),
        "melt": ("available", "flatpak"),
        "ffprobe": ("available", "flatpak"),
        "ffmpeg": ("available", "flatpak"),
    }
    assert by_name["kdenlive"].version == "26.08.3"
    assert any("Flatpak" in n and "/tmp" in n for n in report.notes)
    args = record.read_text().splitlines()
    assert args[:3] == ["run", "--user", "--command=ffmpeg"] and flatpak.APP in args


def test_wrappers_pass_arguments_verbatim_and_private_dirs_on_request(fp, tmp_path):
    env, record = fp
    melt = Path(environment.locate("melt", env) or "")
    assert melt.parent == tmp_path / "cache" / "kdenlive-mcp" / "flatpak-bin"
    assert oct(melt.stat().st_mode & 0o777) == "0o700"
    import subprocess

    hostile = "a b;$(touch pwned)'\"`.mp4"
    subprocess.run([str(melt), "-quiet", hostile], env=dict(env), check=True)
    args = record.read_text().splitlines()
    assert args[:3] == ["run", "--user", "--command=melt"] and hostile in args
    assert not any(a.startswith("--env=") for a in args)
    private = dict(env, KDENLIVE_MCP_PRIVATE_DIRS="1", TMPDIR=str(tmp_path / "t"))
    subprocess.run([str(melt), "x"], env=private, check=True)
    assert f"--env=TMPDIR={tmp_path / 't'}" in record.read_text().splitlines()
    assert not (tmp_path / "pwned").exists()


def test_host_tools_are_preferred(fp, make_stub):
    env, _ = fp
    make_stub("melt", 'echo "melt 7.42.0"')
    assert environment.locate("melt", env) == str(Path(env["PATH"]) / "melt")
    assert "flatpak-bin" in (environment.locate("kdenlive", env) or "")


@pytest.mark.parametrize("hidden", ["/tmp/x/p.kdenlive", "/usr/share/a.mp4", "/var/tmp/b.mp4"])
def test_paths_the_sandbox_cannot_see(hidden):
    assert flatpak.hidden(Path(hidden))


def test_home_paths_are_visible(tmp_path):
    assert not flatpak.hidden(Path.home() / "Videos" / "p.kdenlive")


def test_render_refuses_a_project_the_sandbox_cannot_see(fp, tmp_path, monkeypatch):
    env, _ = fp
    sysdata = tmp_path / "sys" / "kdenlive" / "export"
    sysdata.mkdir(parents=True)
    shutil.copy(FIX / "render" / "profiles.xml", sysdata / "profiles.xml")
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "sys"))
    monkeypatch.setattr(flatpak, "HIDDEN", (str(tmp_path / "p"),))  # pretend it is /tmp
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    (tmp_path / "out").mkdir()
    from mcp_kdenlive import render

    monkeypatch.setattr(render, "supports_cli_render", lambda *_a, **_k: True)
    with pytest.raises(ProjectError) as err:
        render.prepare(
            str(tmp_path / "p" / "kd26-ntsc-timeline.kdenlive"),
            str(tmp_path / "out" / "o.mp4"),
            env=dict(env, XDG_DATA_DIRS=str(tmp_path / "sys")),
        )
    assert err.value.code == "invalid_path"
    assert "Flatpak" in err.value.message


def test_no_flatpak_means_nothing_changes(stub_env, tmp_path):
    env = dict(stub_env, HOME=str(tmp_path / "nohome"))
    assert flatpak.installation(env) is None
    assert environment.locate("melt", env) is None


def test_private_work_dirs_live_in_the_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    work = environment.work_dir("kdenlive-mcp-test-")
    assert work.parent == tmp_path / "c" / "kdenlive-mcp" / "tmp"
    assert oct(work.stat().st_mode & 0o777) == "0o700"


def test_detection_can_be_disabled(fp):
    env, _ = fp
    assert flatpak.installation(dict(env, KDENLIVE_MCP_FLATPAK="off")) is None
