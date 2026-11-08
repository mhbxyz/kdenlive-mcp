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

import os
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
    assert flatpak.hidden(Path(hidden), permissions="host")


def test_home_paths_are_visible(tmp_path):
    assert not flatpak.hidden(Path.home() / "Videos" / "p.kdenlive", permissions="host")


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


def test_no_flatpak_means_nothing_changes(stub_env, tmp_path, monkeypatch):
    monkeypatch.setattr(flatpak, "SYSTEM", tmp_path / "no-system")
    env = dict(stub_env, HOME=str(tmp_path / "nohome"))
    env.pop("KDENLIVE_MCP_FLATPAK")
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


def test_system_installation_and_app_files(tmp_path, monkeypatch):
    system = tmp_path / "var-lib-flatpak"
    (system / "exports" / "bin").mkdir(parents=True)
    (system / "exports" / "bin" / flatpak.APP).write_text("#!/bin/sh\n")
    monkeypatch.setattr(flatpak, "SYSTEM", system)
    env = {"HOME": str(tmp_path / "home")}
    assert flatpak.installation(env) == "system"
    assert flatpak.files_dir(env) is None  # app folder not there
    files = system / "app" / flatpak.APP / "current" / "active" / "files"
    files.mkdir(parents=True)
    assert flatpak.files_dir(env) == files
    launcher = Path(flatpak.wrapper("melt", dict(env, XDG_CACHE_HOME=str(tmp_path / "c"))) or "")
    assert "run --system --command=melt" in launcher.read_text()


def test_user_app_files_and_unknown_tools(fp, tmp_path):
    env, _ = fp
    files = tmp_path / "home" / ".local" / "share" / "flatpak" / "app" / flatpak.APP
    (files / "current" / "active" / "files").mkdir(parents=True)
    assert flatpak.files_dir(env) == files / "current" / "active" / "files"
    assert flatpak.wrapper("bash", env) is None
    assert (
        flatpak.files_dir({"HOME": str(tmp_path / "none"), "KDENLIVE_MCP_FLATPAK": "off"}) is None
    )


# ------------------------------------------------------------------ review cases ------
@pytest.mark.parametrize(
    ("path", "hidden"),
    [
        ("/run/media/me/usb/clip.mp4", False),  # removable drives are visible
        ("/run/user/1000/x", True),
        ("/var/lib/x", True),
        ("/var/home/me/Videos/p.kdenlive", False),  # Silverblue-style homes
        ("/mnt/data/p.kdenlive", False),
    ],
)
def test_hidden_rules(path, hidden):
    assert flatpak.hidden(Path(path), permissions="host") is hidden


def test_restricted_filesystem_permissions(tmp_path):
    home = Path.home()
    assert flatpak.hidden(home / "Videos" / "a.mp4", permissions="home") is False
    assert flatpak.hidden(Path("/mnt/data/a.mp4"), permissions="home") is True
    videos, music = flatpak._xdg_dir("VIDEOS", home), flatpak._xdg_dir("MUSIC", home)
    assert flatpak.hidden(videos / "a.mp4", permissions="xdg-videos") is False
    assert flatpak.hidden(music / "a.mp4", permissions="xdg-videos") is True
    assert flatpak.hidden(Path("/srv/clips/a.mp4"), permissions="/srv/clips") is False
    assert flatpak.hidden(Path("/mnt/data/a.mp4"), permissions="") is True


def test_launcher_survives_odd_paths_and_concurrent_writers(fp, tmp_path, monkeypatch):
    env, _ = fp
    odd = tmp_path / "odd $dir `x`" / "flatpak"
    odd.parent.mkdir()
    odd.write_text('#!/bin/sh\necho odd-flatpak "$@"\n')
    odd.chmod(0o755)
    monkeypatch.setattr(flatpak, "FLATPAK", str(odd))
    import subprocess
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as pool:
        paths = list(pool.map(lambda _: flatpak.wrapper("melt", env), range(32)))
    assert len(set(paths)) == 1
    out = subprocess.run(
        [paths[0], "a b"], env=dict(env), capture_output=True, text=True, check=True
    ).stdout
    assert out.startswith("odd-flatpak run --user --command=melt")
    assert not list((tmp_path / "cache" / "kdenlive-mcp" / "flatpak-bin").glob("*.tmp*"))
    assert oct((tmp_path / "cache" / "kdenlive-mcp").stat().st_mode & 0o777) == "0o700"


def test_host_mlt_variables_do_not_enter_the_sandbox(fp, tmp_path):
    env, _ = fp
    import subprocess

    melt = environment.locate("melt", env) or ""
    subprocess.run([melt, "x"], env=dict(env, MLT_PROFILES_PATH="/host/profiles"), check=True)
    text = (tmp_path / "flatpak.args").read_text()
    assert "MLT_PROFILES_PATH" not in text  # the stub records TMPDIR only; see wrapper below
    assert "unset" in Path(melt).read_text() and "MLT_" in Path(melt).read_text()


def test_only_launchers_from_our_cache_count(tmp_path):
    other = tmp_path / "flatpak-bin" / "melt"
    assert not flatpak.is_wrapper(str(other))


def test_app_profiles_and_presets_are_used(fp, tmp_path, monkeypatch):
    env, _ = fp
    files = tmp_path / "home" / ".local" / "share" / "flatpak" / "app" / flatpak.APP
    app = files / "current" / "active" / "files" / "share"
    (app / "mlt-7" / "profiles").mkdir(parents=True)
    shutil.copy(FIX / "profiles" / "atsc_720p_2997", app / "mlt-7" / "profiles")
    (app / "kdenlive" / "export").mkdir(parents=True)
    shutil.copy(FIX / "render" / "profiles.xml", app / "kdenlive" / "export" / "profiles.xml")
    user = tmp_path / "home" / ".var" / "app" / flatpak.APP / "data" / "kdenlive" / "export"
    user.mkdir(parents=True)
    (user / "customprofiles.xml").write_text(
        '<profiles><profile name="Mine" extension="mp4" args="f=mp4"/></profiles>'
    )
    from mcp_kdenlive import create, render

    assert create.profiles_dir(env) == app / "mlt-7" / "profiles"
    files_read = [p for p, _ in render.preset_files(env)]
    assert files_read[0] == app / "kdenlive" / "export" / "profiles.xml"
    assert user / "customprofiles.xml" in files_read
    assert "Mine" in {p.name for p in render.list_presets(env).presets}


def test_profile_names_are_not_checked_as_paths(fp, tmp_path, monkeypatch):
    monkeypatch.chdir("/")
    environment.check_visible(Path("atsc_1080p_25"), environment.locate("melt", fp[0]), "x")


# ------------------------------------------------------------------ real Flatpak -------
@pytest.mark.integration
def test_real_flatpak_kdenlive(tmp_path, monkeypatch):
    """With the Flathub app installed for the user (skipped otherwise): detection, a project
    created and rendered by both engines, from a folder the sandbox can see."""
    import uuid

    real_home = Path.home()
    monkeypatch.delenv("KDENLIVE_MCP_FLATPAK", raising=False)
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")  # flatpak run needs it
    if flatpak.installation() is None:
        pytest.skip("no Flatpak org.kde.kdenlive installed")
    from mcp_kdenlive import create, render

    work = real_home / ".cache" / f"kdenlive-mcp-test-{uuid.uuid4().hex[:8]}"
    try:
        shutil.copytree(FIX / "kd26" / "media", work / "media")
        monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(work / "cache"))
        monkeypatch.setenv("KDENLIVE_MCP_ALLOWED_ROOTS", str(work))
        report = environment.diagnose()
        assert {t.source for t in report.tools} == {"flatpak"}
        assert all(t.status.value == "available" for t in report.tools)
        created = create.create_project(
            str(work / "p.kdenlive"), str(work / "media" / "ntsc.mp4"), "atsc_720p_2997"
        )
        assert created.clip_duration_frames == 60
        for engine in ("kdenlive", "melt"):
            plan = render.prepare(
                str(work / "p.kdenlive"), str(work / f"{engine}.mp4"), engine=engine
            )
            result = render.render(plan, timeout=300)
            assert result.state is render.RenderState.SUCCEEDED, (engine, result.log_tail)
        with pytest.raises(ProjectError) as err:
            render.prepare(str(FIX / "kd26" / "kd26-ntsc.kdenlive"), str(work / "x.mp4"))
        assert err.value.code in {"invalid_path", "path_not_allowed"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
