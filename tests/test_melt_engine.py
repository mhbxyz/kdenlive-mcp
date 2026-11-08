"""MLT (melt) render engine (#42): Kdenlive preset arguments resolved to melt consumer properties.

Preset definitions are a subset of Kdenlive 26.08.2's own profiles.xml
(tests/fixtures/render/profiles.xml). Kdenlive replaces `%quality`, `%audioquality`, `%bitrate`
and `%audiobitrate` with the preset's defaults, joins `+'k'` suffixes, appends the default
speed option, and fills `%dv_standard` from the project's frame rate.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from mcp_kdenlive import render
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.timecode import FrameRate

FIX = Path(__file__).parent / "fixtures"
PRESETS = FIX / "render" / "profiles.xml"
NTSC = FrameRate(30000, 1001)
PAL = FrameRate(25, 1)


def attrs(name: str) -> dict[str, str]:
    found = render.preset_definitions([PRESETS])
    return found[name]


@pytest.mark.parametrize(
    ("name", "rate", "expected"),
    [
        (
            "MP4-H264/AAC",
            NTSC,
            ["f=mp4", "movflags=+faststart", "vcodec=libx264", "crf=23", "g=15", "acodec=aac",
             "ab=160k", "preset=veryfast"],
        ),
        (
            "WebM-VP9/Opus (libre)",
            NTSC,
            ["f=webm", "vcodec=libvpx-vp9", "crf=25", "vb=15M", "qcomp=1", "g=15", "row-mt=1",
             "tile-columns=4", "frame-parallel=1", "acodec=libopus", "compression_level=5"],
        ),
        (
            "MPEG-2",
            NTSC,
            ["properties=MPEG-2", "qscale=5", "aq=3", "subq=3", "cmp=1", "subcmp=1", "trellis=1",
             "bf=2"],
        ),
        ("WAV", NTSC, ["properties=WAV", "video_off=1"]),
        ("AC3", NTSC, ["ab=160k", "vn=1", "video_off=1"]),
        (
            "NVENC H264 ABR",
            NTSC,
            ["f=mp4", "vcodec=h264_nvenc", "vb=6000k", "acodec=aac", "ab=160k"],
        ),
        (
            "VOB (DVD)",
            PAL,
            ["properties=dv_pal/DVD", "mlt_profile=dv_pal", "f=dvd", "vcodec=mpeg2video",
             "acodec=ac3", "qscale=5", "ab=160k", "subq=3", "cmp=1", "subcmp=1", "trellis=1",
             "bf=2"],
        ),
    ],
)  # fmt: skip
def test_preset_arguments_resolve_like_kdenlive(name, rate, expected):
    assert render.melt_consumer_args(attrs(name), rate) == expected


@pytest.mark.parametrize(
    "args",
    [
        "f=mp4 target=/home/x/escaped.mp4",
        "f=mp4 /home/x/extra-input.mp4",
        "f=mp4 -consumer xml:/home/x/out.xml",
        "f=mp4 resource=http://attacker.example/x",
        "f=mp4 vcodec=http://attacker.example/x",
    ],
)
def test_unsafe_preset_arguments_are_refused(args):
    """Custom presets (e.g. downloaded ones) must not redirect the output or add inputs."""
    with pytest.raises(ProjectError) as err:
        render.melt_consumer_args({"name": "evil", "args": args}, NTSC)
    assert err.value.code == "unknown_preset"


def test_image_sequence_presets_are_refused_for_melt():
    with pytest.raises(ProjectError) as err:
        render.melt_consumer_args(
            {"name": "PNG with alpha", "extension": "png", "args": "vcodec=png"}, NTSC
        )
    assert err.value.code == "unknown_preset"


def test_dv_standard_follows_the_frame_rate():
    assert "properties=dv_ntsc/DVD" in render.melt_consumer_args(attrs("VOB (DVD)"), NTSC)


def test_unknown_placeholders_are_refused():
    weird = dict(attrs("WAV"), args="properties=WAV x=%mystery")
    with pytest.raises(ProjectError) as err:
        render.melt_consumer_args(weird, NTSC)
    assert err.value.code == "unknown_preset"


def test_quality_falls_back_to_the_middle_of_the_range():
    no_default = {k: v for k, v in attrs("MP4-H264/AAC").items() if k != "defaultquality"}
    assert "crf=30" in render.melt_consumer_args(no_default, NTSC)


# ------------------------------------------------------------------ engine choice -----
@pytest.fixture
def no_cli_kdenlive(tmp_path, make_stub, stub_env, monkeypatch):
    """Kdenlive 23.08-like: no --render. melt writes a small file to the consumer target."""
    sysdata = tmp_path / "sys" / "kdenlive" / "export"
    sysdata.mkdir(parents=True)
    shutil.copy(PRESETS, sysdata / "profiles.xml")
    make_stub("kdenlive", 'echo "Usage: kdenlive [options] file"')
    args_file = tmp_path / "melt.args"
    make_stub(
        "melt",
        f'printf "%s\\n" "$@" > "{args_file}"\n'
        'for a; do case "$a" in avformat:*) printf x > "${a#avformat:}";; esac; done',
    )
    env = dict(stub_env, XDG_DATA_DIRS=str(tmp_path / "sys"), XDG_DATA_HOME=str(tmp_path / "u"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    return env, args_file, tmp_path / "p"


def test_auto_engine_uses_melt_without_cli_render(no_cli_kdenlive):
    env, args_file, ws = no_cli_kdenlive
    plan = render.prepare(str(ws / "kd26-ntsc-timeline.kdenlive"), str(ws / "out.mp4"), env=env)
    assert plan.engine == "melt"
    assert any("not Kdenlive's own render" in w for w in plan.warnings)
    run = render.RenderRun(plan, env)
    run.start()
    assert run.running is not None
    run.running.proc.wait(10)
    args = args_file.read_text().splitlines()
    assert f"avformat:{plan.temp_output}" in args
    assert "crf=23" in args and "real_time=-1" in args
    assert "rescale=bilinear" in args and "deinterlacer=onefield" in args  # Kdenlive's defaults
    # melt reads a copy whose `root` is the folder Kdenlive would use (the stored one is gone)
    scene = Path(args[0])
    assert scene.parent == run.workdir
    assert f'root="{ws}"' in scene.read_text(encoding="utf-8")
    run.cleanup()


def test_kdenlive_engine_is_refused_without_cli_render(no_cli_kdenlive):
    env, _, ws = no_cli_kdenlive
    with pytest.raises(ProjectError) as err:
        render.prepare(
            str(ws / "kd26-ntsc-timeline.kdenlive"), str(ws / "o.mp4"), env=env, engine="kdenlive"
        )
    assert err.value.code == "unsupported_kdenlive"


def test_subtitled_projects_use_melt_in_auto_mode(tmp_path, make_stub, stub_env, monkeypatch):
    sysdata = tmp_path / "sys" / "kdenlive" / "export"
    sysdata.mkdir(parents=True)
    shutil.copy(PRESETS, sysdata / "profiles.xml")
    make_stub("kdenlive", 'echo "--render --render-preset"')
    make_stub("melt", "exit 0")
    env = dict(stub_env, XDG_DATA_DIRS=str(tmp_path / "sys"), XDG_DATA_HOME=str(tmp_path / "u"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    project = tmp_path / "p" / "kd26-subtitle.kdenlive"
    plan = render.prepare(str(project), str(tmp_path / "p" / "o.mp4"), env=env)
    assert plan.engine == "melt"
    forced = render.prepare(
        str(project), str(tmp_path / "p" / "o2.mp4"), env=env, engine="kdenlive"
    )
    assert forced.engine == "kdenlive"
    assert any("subtitles" in w for w in forced.warnings)


def test_engine_value_is_validated(no_cli_kdenlive):
    env, _, ws = no_cli_kdenlive
    with pytest.raises(ProjectError) as err:
        render.prepare(
            str(ws / "kd26-ntsc-timeline.kdenlive"), str(ws / "o.mp4"), env=env, engine="ffmpeg"
        )
    assert err.value.code == "invalid_argument"


def test_melt_progress_is_read_from_its_log(no_cli_kdenlive):
    env, _, ws = no_cli_kdenlive
    plan = render.prepare(str(ws / "kd26-ntsc-timeline.kdenlive"), str(ws / "o.mp4"), env=env)
    run = render.RenderRun(plan, env)
    run.log_path.write_text("Current Frame:         10, percentage:         16\r"
                            "Current Frame:         30, percentage:         50\r")  # fmt: skip
    assert run.progress() == (50, 30)
    run.cleanup()
    assert os.path.exists(str(plan.project_path))


# ------------------------------------------------------------------ real tools ---------
needs_melt = pytest.mark.skipif(
    shutil.which("melt") is None or shutil.which("ffprobe") is None,
    reason="needs melt and ffprobe",
)


@pytest.mark.integration
@needs_melt
@pytest.mark.parametrize(
    "fixture", ["kd26-ntsc-timeline.kdenlive", "kd26-mixes.kdenlive", "kd26-subtitle.kdenlive"]
)
def test_melt_engine_renders_kdenlive_projects(fixture, tmp_path):
    """Runs in CI against Ubuntu's MLT 7.22 (no kdenlive --render there) and locally."""
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    plan = render.prepare(str(tmp_path / "p" / fixture), str(tmp_path / "o.mp4"), engine="melt")
    result = render.render(plan, timeout=300)
    assert result.state is render.RenderState.SUCCEEDED, result.error or result.log_tail
    assert result.engine == "melt" and result.validation and result.validation.ok
    assert any("not Kdenlive's own render" in w for w in result.warnings)
    # picture and sound, not a blank render (projects whose stored root is gone, #42 review)
    assert _mean_rgb(tmp_path / "o.mp4", 0.5) != (255, 255, 255)
    assert max(_mean_rgb(tmp_path / "o.mp4", 0.5)) > 40
    assert _mean_volume(tmp_path / "o.mp4") > -60


@pytest.mark.integration
@needs_melt
def test_melt_and_kdenlive_engines_agree(tmp_path):
    """Local comparison with Kdenlive's own render (skipped where kdenlive --render is missing)."""
    from mcp_kdenlive.environment import locate

    exe = locate("kdenlive", os.environ)
    if exe is None or not render.supports_cli_render(exe):
        pytest.skip("needs a Kdenlive with --render (24.02+)")
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    project = str(tmp_path / "p" / "kd26-mixes.kdenlive")
    outputs = {}
    for engine in ("melt", "kdenlive"):
        out = tmp_path / f"{engine}.mp4"
        result = render.render(render.prepare(project, str(out), engine=engine), timeout=300)
        assert result.state is render.RenderState.SUCCEEDED, (engine, result.log_tail)
        outputs[engine] = result.validation
    melt, kd = outputs["melt"], outputs["kdenlive"]
    assert melt and kd
    assert abs((melt.duration_seconds or 0) - (kd.duration_seconds or 0)) < 0.1
    assert (melt.video_streams, melt.audio_streams) == (kd.video_streams, kd.audio_streams)


def _mean_rgb(path: Path, t: float) -> tuple[int, ...]:
    import subprocess

    out = subprocess.run(
        [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path),
         "-frames:v", "1", "-vf", "scale=1:1:flags=area,format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True, check=True, timeout=60,
    ).stdout  # fmt: skip
    return tuple(out[:3])


def _mean_volume(path: Path) -> float:
    import re
    import subprocess

    err = subprocess.run(
        [shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-i", str(path), "-vn",
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, check=True, timeout=60,
    ).stderr  # fmt: skip
    match = re.search(r"mean_volume: (-?[\d.]+) dB", err)
    return float(match.group(1)) if match else -91.0
