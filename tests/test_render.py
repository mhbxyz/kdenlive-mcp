"""Render preparation, execution and validation (stubbed Kdenlive) plus a real smoke test."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from mcp_kdenlive import media, render
from mcp_kdenlive.kdenlive_doc import ProjectError
from mcp_kdenlive.media import ProbeInfo, StreamInfo
from mcp_kdenlive.render import RenderState

FIX = Path(__file__).parent / "fixtures"
SLEEP = shutil.which("sleep")  # resolved before fixtures replace PATH
PROJECT = (FIX / "kd26" / "kd26-ntsc-timeline.kdenlive").resolve()

PRESETS_XML = """<?xml version="1.0"?>
<profiles version="0.1">
 <group renderer="avformat" type="av" id="generic">
  <name>Generic</name>
  <profile name="MP4-H264/AAC" extension="mp4" args="f=mp4"/>
  <profile name="WebM-VP8/Vorbis (libre)" extension="webm" args="f=webm"/>
 </group>
</profiles>"""

CUSTOM_XML = """<?xml version="1.0"?>
<profiles version="0.1"><profile name="MP4-H264/AAC" category="Mine" extension="mp4" args="crf=1"/>
<profile name="My Preset" category="Mine" extension="mkv" args="f=matroska"/></profiles>"""


@pytest.fixture
def env(tmp_path, make_stub, stub_env):
    """Fake Kdenlive install: preset files + a kdenlive stub on PATH; returns the environment."""
    sysdata = tmp_path / "sys"
    (sysdata / "kdenlive" / "export").mkdir(parents=True)
    (sysdata / "kdenlive" / "export" / "profiles.xml").write_text(PRESETS_XML)
    userdata = tmp_path / "userdata"
    (userdata / "kdenlive" / "export").mkdir(parents=True)
    make_stub("kdenlive", HELP_STUB)
    e = dict(stub_env, XDG_DATA_DIRS=str(sysdata), XDG_DATA_HOME=str(userdata))
    for key, value in e.items():
        os.environ[key] = value  # render.render() reads os.environ for the child
    yield e


@pytest.fixture(autouse=True)
def _restore_environ():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def fake_probe(monkeypatch):
    """ffprobe stand-in: a 2.002 s file with one video and one audio stream."""

    def probe(path, *_a, **_k):
        if not Path(path).is_file():
            return ProbeInfo(ok=False, error="missing")
        return ProbeInfo(
            ok=True,
            format="mp4",
            duration_seconds=2.002,
            streams=[
                StreamInfo(index=0, type="video", codec="h264"),
                StreamInfo(index=1, type="audio", codec="aac"),
            ],
        )

    monkeypatch.setattr(media, "probe_file", probe)
    return probe


# Answers --help like Kdenlive 26.08 so the capability check passes, then runs `body`.
HELP_STUB = 'if [ "$1" = --help ]; then echo "  --render  --render-preset <p>"; exit 0; fi'


def kdenlive_stub(make_stub, body: str) -> None:
    # Arguments: --render --render-preset NAME PROJECT OUTPUT
    make_stub("kdenlive", HELP_STUB + "\n" + body)


# ------------------------------------------------------------------- presets ----------
def test_presets_system_and_custom_override(env, tmp_path):
    (tmp_path / "userdata" / "kdenlive" / "export" / "customprofiles.xml").write_text(CUSTOM_XML)
    listed = render.list_presets(env)
    names = {p.name: p for p in listed.presets}
    assert set(names) == {"MP4-H264/AAC", "WebM-VP8/Vorbis (libre)", "My Preset"}
    assert names["MP4-H264/AAC"].custom is True  # customprofiles.xml overrides the system preset
    assert names["WebM-VP8/Vorbis (libre)"].extension == "webm"
    assert listed.sources[-1].endswith("customprofiles.xml")


def test_no_preset_files(stub_env):
    listed = render.list_presets(
        dict(stub_env, XDG_DATA_DIRS="/nonexistent", XDG_DATA_HOME="/nonexistent")
    )
    assert listed.presets == [] and any("No Kdenlive preset file" in n for n in listed.notes)


def test_real_installation_presets():
    if shutil.which("kdenlive") is None:
        pytest.skip("Kdenlive is not installed")
    names = {p.name for p in render.list_presets().presets}
    assert render.DEFAULT_PRESET in names


# ------------------------------------------------------------------- preflight --------
@pytest.mark.parametrize(
    ("output", "kwargs", "code"),
    [
        ("relative.mp4", {}, "invalid_output"),
        ("{tmp}/missing-dir/out.mp4", {}, "invalid_output"),
        ("{tmp}", {}, "invalid_output"),
        ("{project}", {"overwrite": True}, "invalid_output"),
        ("{tmp}/out.mp4", {"preset": "No Such Preset"}, "unknown_preset"),
    ],
)
def test_preflight_errors(env, tmp_path, output, kwargs, code):
    out = output.format(tmp=tmp_path, project=PROJECT)
    with pytest.raises(ProjectError) as err:
        render.prepare(str(PROJECT), out, env=env, **kwargs)
    assert err.value.code == code


def test_existing_output_needs_overwrite(env, tmp_path):
    existing = tmp_path / "out.mp4"
    existing.write_bytes(b"old")
    with pytest.raises(ProjectError) as err:
        render.prepare(str(PROJECT), str(existing), env=env)
    assert err.value.code == "output_exists"
    assert render.prepare(str(PROJECT), str(existing), overwrite=True, env=env).overwrite


def test_output_cannot_be_project_media(env):
    media_file = PROJECT.parent / "media" / "ntsc.mp4"
    with pytest.raises(ProjectError) as err:
        render.prepare(str(PROJECT), str(media_file), overwrite=True, env=env)
    assert err.value.code == "invalid_output"


def test_missing_media_is_refused_unless_allowed(env, tmp_path):
    project = (FIX / "upstream" / "test-missing.kdenlive").resolve()
    with pytest.raises(ProjectError) as err:
        render.prepare(str(project), str(tmp_path / "o.mp4"), env=env)
    assert err.value.code == "missing_media"
    plan = render.prepare(str(project), str(tmp_path / "o.mp4"), allow_missing_media=True, env=env)
    assert any("missing" in w for w in plan.warnings)


def test_old_kdenlive_without_cli_render(env, tmp_path, make_stub):
    make_stub("kdenlive", 'echo "Usage: kdenlive [options] file"; exit 0')  # like 23.08
    with pytest.raises(ProjectError) as err:
        render.prepare(str(PROJECT), str(tmp_path / "o.mp4"), env=env)
    assert err.value.code == "unsupported_kdenlive"


def test_kdenlive_missing(stub_env, tmp_path):
    with pytest.raises(ProjectError) as err:
        render.prepare(str(PROJECT), str(tmp_path / "o.mp4"), env=stub_env)
    assert err.value.code == "dependency_missing"


def test_nonexistent_project_is_caught_before_kdenlive(env, tmp_path):
    # kdenlive --render crashes (SIGSEGV) on a nonexistent project; we never get there.
    with pytest.raises(ProjectError) as err:
        render.prepare(str(tmp_path / "nope.kdenlive"), str(tmp_path / "o.mp4"), env=env)
    assert err.value.code == "not_found"


def test_extension_mismatch_is_a_warning(env, tmp_path):
    plan = render.prepare(str(PROJECT), str(tmp_path / "o.mkv"), env=env)
    assert any(".mp4" in w for w in plan.warnings)


# ------------------------------------------------------------------- execution --------
def test_successful_render_moves_validated_output(env, tmp_path, make_stub, fake_probe):
    record = tmp_path / "record"
    kdenlive_stub(
        make_stub,
        f'printf "%s\\n" "$@" > "{record}"; '
        'echo "QT=$QT_QPA_PLATFORM CFG=$XDG_CONFIG_HOME BUS=$DBUS_SESSION_BUS_ADDRESS"'
        f' > "{record}.env"; echo frames > "$5"; exit 0',
    )
    out = tmp_path / "dir with space é" / "rendu final.mp4"
    out.parent.mkdir()
    result = render.render(render.prepare(str(PROJECT), str(out), env=env), timeout=30)
    assert result.state is RenderState.SUCCEEDED, result.error
    assert result.process_completed and result.validation and result.validation.ok
    assert out.read_text() == "frames\n"
    args = record.read_text().splitlines()
    assert args[:3] == ["--render", "--render-preset", "MP4-H264/AAC"]
    assert args[3] == str(PROJECT) and args[4] != str(out)  # rendered to a temp file first
    child_env = (tmp_path / "record.env").read_text()
    # Only the variables under test are recorded: never dump a whole environment in tests.
    assert "QT=offscreen " in child_env
    assert "kdenlive-mcp-render-" in child_env.split("CFG=")[1]
    assert "no-session-bus" in child_env
    assert sorted(p.name for p in out.parent.iterdir()) == ["rendu final.mp4"]


def test_success_without_output_is_a_failure(env, tmp_path, make_stub, fake_probe):
    kdenlive_stub(make_stub, "exit 0")
    result = render.render(render.prepare(str(PROJECT), str(tmp_path / "o.mp4"), env=env))
    assert result.state is RenderState.FAILED and "no output" in (result.error or "")
    assert result.process_completed is True and result.validation is None


def test_failed_process_keeps_log_tail_and_cleans_up(env, tmp_path, make_stub, fake_probe):
    kdenlive_stub(make_stub, 'echo partial > "$5"; echo "Error: codec not found" >&2; exit 3')
    out = tmp_path / "o.mp4"
    result = render.render(render.prepare(str(PROJECT), str(out), env=env))
    assert result.state is RenderState.FAILED and result.exit_code == 3
    assert any("codec not found" in line for line in result.log_tail)
    assert list(tmp_path.glob("*.partial*")) == [] and not out.exists()


def test_invalid_output_is_not_published(env, tmp_path, make_stub, monkeypatch):
    kdenlive_stub(make_stub, 'echo x > "$5"')
    monkeypatch.setattr(
        media,
        "probe_file",
        lambda *_a, **_k: ProbeInfo(
            ok=True,
            format="mp4",
            duration_seconds=9.0,
            streams=[StreamInfo(index=0, type="video", codec="h264")],
        ),
    )
    out = tmp_path / "o.mp4"
    result = render.render(render.prepare(str(PROJECT), str(out), env=env))
    assert result.state is RenderState.FAILED and result.process_completed
    assert result.validation and "differs from the timeline" in result.validation.issues[0]
    assert not out.exists()


def test_overwrite_replaces_only_after_success(env, tmp_path, make_stub, fake_probe):
    out = tmp_path / "o.mp4"
    out.write_text("previous")
    kdenlive_stub(make_stub, "exit 2")
    failed = render.render(render.prepare(str(PROJECT), str(out), overwrite=True, env=env))
    assert failed.state is RenderState.FAILED and out.read_text() == "previous"
    kdenlive_stub(make_stub, 'echo new > "$5"')
    done = render.render(render.prepare(str(PROJECT), str(out), overwrite=True, env=env))
    assert done.state is RenderState.SUCCEEDED and out.read_text() == "new\n"


def test_timeout_stops_the_process_group(env, tmp_path, make_stub, fake_probe):
    pidfile = tmp_path / "child.pid"
    kdenlive_stub(make_stub, f'{SLEEP} 60 & echo $! > "{pidfile}"; echo x > "$5"; wait')
    result = render.render(
        render.prepare(str(PROJECT), str(tmp_path / "o.mp4"), env=env), timeout=1
    )
    assert result.state is RenderState.FAILED and "exceeded" in (result.error or "")
    child = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        for _ in range(50):
            os.kill(child, 0)
            time.sleep(0.05)
    assert list(tmp_path.glob("*.partial*")) == []


def test_project_is_never_modified(env, tmp_path, make_stub, fake_probe):
    kdenlive_stub(make_stub, 'echo x > "$5"')
    before = PROJECT.read_bytes()
    render.render(render.prepare(str(PROJECT), str(tmp_path / "o.mp4"), env=env))
    assert PROJECT.read_bytes() == before


# ------------------------------------------------------------------- real Kdenlive ----
@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("kdenlive") is None or shutil.which("ffprobe") is None,
    reason="Kdenlive and ffprobe are required",
)
def test_real_render_end_to_end(tmp_path):
    project = tmp_path / "p.kdenlive"
    shutil.copy(PROJECT, project)
    shutil.copytree(PROJECT.parent / "media", tmp_path / "media")
    before = project.read_bytes()
    out = tmp_path / "out é.mp4"
    if not render.supports_cli_render(shutil.which("kdenlive") or ""):
        # e.g. Kdenlive 23.08 in CI: the server must refuse cleanly instead of failing later.
        with pytest.raises(ProjectError) as err:
            render.prepare(str(project), str(out))
        assert err.value.code == "unsupported_kdenlive"
        return
    result = render.render(render.prepare(str(project), str(out)), timeout=300)
    assert result.state is RenderState.SUCCEEDED, (result.error, result.log_tail)
    assert result.validation and result.validation.video_streams == 1
    assert result.validation.duration_seconds == pytest.approx(2.002, abs=0.25)
    assert project.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["media", "out é.mp4", "p.kdenlive"]
