"""Project creation: structure, timing, safety (stubbed melt) and real-tool verification."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mcp_kdenlive import create, kdenlive_doc, media, project, render
from mcp_kdenlive.kdenlive_doc import ProjectError

FIX = Path(__file__).parent / "fixtures"
MEDIA = FIX / "kd26" / "media"


def copy_media(tmp_path: Path) -> Path:
    shutil.copytree(MEDIA, tmp_path / "media")
    return tmp_path / "media"


# ------------------------------------------------------------- structure (stubbed) ----
def test_create_av_project_structure(stub_melt, tmp_path):
    env, args_file = stub_melt
    mdir = copy_media(tmp_path)
    out = tmp_path / "new.kdenlive"
    result = create.create_project(str(out), str(mdir / "ntsc.mp4"), "atsc_720p_2997", env=env)
    assert result.tracks == ["A1", "V1"] and result.clip_duration_frames == 60
    assert result.media_reference == "media/ntsc.mp4" and result.warnings == []
    doc = kdenlive_doc.load(str(out))
    assert doc.generation == 5 and doc.rate.num == 30000 and doc.rate.den == 1001
    assert doc.root.get("root") == str(tmp_path)
    tl = project.timeline(doc)
    clips = {t.label: t.clips for t in tl.tracks if t.clips}
    assert set(clips) == {"A1", "V1"}
    for (clip,) in clips.values():
        assert (clip.start, clip.end, clip.source_in, clip.source_out) == (0, 60, 0, 59)
        assert clip.bin_id == "4" and clip.resource == "media/ntsc.mp4"
    seq = kdenlive_doc.props(doc.sequence_tractors()[0])
    assert '"type": "AVSplit"' in seq["kdenlive:sequenceproperties.groups"]
    assert seq["kdenlive:duration"] == "00:00:02.002"
    bin_media = [i for i in media.media_report(doc).items if i.kind == "file"]
    assert len(bin_media) == 1 and bin_media[0].status is media.MediaStatus.OK
    expected_hash, size = create.kdenlive_file_hash(mdir / "ntsc.mp4")
    chain = next(e for e in doc.root if e.tag == "chain")
    props = kdenlive_doc.props(chain)
    assert props["kdenlive:file_hash"] == expected_hash and props["kdenlive:file_size"] == str(size)
    assert f"avformat:{mdir / 'ntsc.mp4'}" in args_file.read_text().splitlines()


def test_audio_only_goes_to_a1(stub_melt, tmp_path):
    env, _ = stub_melt
    mdir = copy_media(tmp_path)
    result = create.create_project(
        str(tmp_path / "a.kdenlive"), str(mdir / "tone.wav"), "atsc_1080p_25", env=env
    )
    assert result.tracks == ["A1"]
    seq = kdenlive_doc.props(kdenlive_doc.load(str(tmp_path / "a.kdenlive")).sequence_tractors()[0])
    assert "AVSplit" not in seq.get("kdenlive:sequenceproperties.groups", "")


def test_media_outside_project_dir_is_absolute_with_warning(stub_melt, tmp_path):
    env, _ = stub_melt
    mdir = copy_media(tmp_path)
    (tmp_path / "projects").mkdir()
    result = create.create_project(
        str(tmp_path / "projects" / "p.kdenlive"), str(mdir / "ntsc.mp4"), "atsc_720p_2997", env=env
    )
    assert result.media_reference == str(mdir / "ntsc.mp4") and result.warnings


def test_existing_output_is_protected(stub_melt, tmp_path):
    env, _ = stub_melt
    mdir = copy_media(tmp_path)
    out = tmp_path / "p.kdenlive"
    out.write_text("precious")
    with pytest.raises(ProjectError) as err:
        create.create_project(str(out), str(mdir / "ntsc.mp4"), "atsc_720p_2997", env=env)
    assert err.value.code == "output_exists" and out.read_text() == "precious"
    create.create_project(
        str(out), str(mdir / "ntsc.mp4"), "atsc_720p_2997", overwrite=True, env=env
    )
    assert out.read_text().startswith("<?xml")


def test_failure_leaves_nothing_behind(stub_melt, tmp_path, make_stub):
    env, _ = stub_melt
    mdir = copy_media(tmp_path)
    make_stub("melt", 'echo "cannot open" >&2; exit 1')
    out = tmp_path / "p.kdenlive"
    with pytest.raises(ProjectError) as err:
        create.create_project(str(out), str(mdir / "ntsc.mp4"), "atsc_720p_2997", env=env)
    assert err.value.code == "media_unreadable"
    assert not out.exists()
    assert [p.name for p in tmp_path.iterdir() if p.suffix in (".kdenlive", ".tmp")] == []


@pytest.mark.parametrize(
    ("output", "media_name", "profile", "code"),
    [
        ("relative.kdenlive", "ntsc.mp4", "atsc_720p_2997", "invalid_output"),
        ("{tmp}/p.mp4", "ntsc.mp4", "atsc_720p_2997", "invalid_output"),
        ("{tmp}/nodir/p.kdenlive", "ntsc.mp4", "atsc_720p_2997", "invalid_output"),
        ("{tmp}/p.kdenlive", "missing.mp4", "atsc_720p_2997", "media_unreadable"),
        ("{tmp}/p.kdenlive", "ntsc.mp4", "no_such_profile", "unknown_profile"),
        ("{tmp}/p.kdenlive", "ntsc.mp4", "../etc/passwd", "unknown_profile"),
    ],
)
def test_invalid_inputs(stub_melt, tmp_path, output, media_name, profile, code):
    env, _ = stub_melt
    mdir = copy_media(tmp_path)
    with pytest.raises(ProjectError) as err:
        create.create_project(output.format(tmp=tmp_path), str(mdir / media_name), profile, env=env)
    assert err.value.code == code


def test_auto_profile_matching(stub_melt):
    env, _ = stub_melt
    probe = media.ProbeInfo(
        ok=True,
        streams=[
            media.StreamInfo(
                index=0, type="video", codec="h264", width=1280, height=720, frame_rate="30000/1001"
            )
        ],
    )
    assert create.matching_profile(probe, env) == "atsc_720p_2997"
    odd = media.ProbeInfo(
        ok=True,
        streams=[
            media.StreamInfo(
                index=0, type="video", codec="h264", width=320, height=180, frame_rate="30000/1001"
            )
        ],
    )
    with pytest.raises(ProjectError) as err:
        create.matching_profile(odd, env)
    assert err.value.code == "profile_required"


def test_hash_matches_kdenlive_rule(tmp_path):
    small = tmp_path / "s.bin"
    small.write_bytes(b"a" * 1000)
    big = tmp_path / "b.bin"
    big.write_bytes(b"x" * 1_000_000 + b"middle" * 200_000 + b"y" * 1_000_000)
    import hashlib

    assert create.kdenlive_file_hash(small) == (
        hashlib.md5(b"a" * 1000, usedforsecurity=False).hexdigest(),
        1000,
    )
    expected = hashlib.md5(b"x" * 1_000_000 + b"y" * 1_000_000, usedforsecurity=False).hexdigest()
    assert create.kdenlive_file_hash(big)[0] == expected  # first + last MB only
    # Kdenlive 26.08.2 stored this exact hash for the fixture media:
    assert create.kdenlive_file_hash(MEDIA / "ntsc.mp4")[0] == "962afa417921dfa965a9be81585c582c"


# ------------------------------------------------------------- real tools -------------
@pytest.mark.integration
@pytest.mark.skipif(shutil.which("melt") is None, reason="melt is not installed")
@pytest.mark.parametrize(
    ("media_name", "profile", "frames"),
    [
        ("ntsc.mp4", "atsc_720p_2997", 60),
        ("pal clip é.mp4", "atsc_1080p_25", 50),
        ("tone.wav", "atsc_1080p_25", 75),
    ],
)
def test_real_melt_creation_and_render(tmp_path, media_name, profile, frames):
    mdir = copy_media(tmp_path)
    out = tmp_path / "p.kdenlive"
    result = create.create_project(str(out), str(mdir / media_name), profile)
    assert result.clip_duration_frames == frames
    tl = project.timeline(kdenlive_doc.load(str(out)))
    assert tl.duration_frames == frames
    if (
        shutil.which("ffprobe")
        and shutil.which("kdenlive")
        and render.supports_cli_render(shutil.which("kdenlive") or "")
    ):
        rendered = render.render(render.prepare(str(out), str(tmp_path / "r.mp4")), timeout=300)
        assert rendered.state is render.RenderState.SUCCEEDED, (rendered.error, rendered.log_tail)
