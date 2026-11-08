"""Project inspection against real Kdenlive-written files (see tests/fixtures/PROVENANCE.md)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from mcp_kdenlive import kdenlive_doc, project
from mcp_kdenlive.kdenlive_doc import ProjectError

FIX = Path(__file__).parent / "fixtures"
UP = FIX / "upstream"
KD26 = FIX / "kd26"


def load(path: Path) -> kdenlive_doc.KdenliveDocument:
    return kdenlive_doc.load(str(path.resolve()))


def clips_of(timeline: project.Timeline) -> list[tuple]:
    return [
        (t.label, c.start, c.end, c.source_in, c.source_out, c.name)
        for t in timeline.tracks
        for c in t.clips
    ]


# ------------------------------------------------------------- Kdenlive 26.08.2 files --
def test_ntsc_project_summary():
    summary = project.summarize(load(KD26 / "kd26-ntsc-timeline.kdenlive"))
    assert summary.kdenlive_version == "26.08.2"
    assert summary.document_version == "1.1"
    assert summary.format_generation == 5
    assert (summary.profile.frame_rate.num, summary.profile.frame_rate.den) == (30000, 1001)
    assert (summary.profile.width, summary.profile.height) == (1280, 720)
    assert summary.bin_clip_count == 3
    (seq,) = summary.sequences
    assert seq.active and seq.name == "Sequence 1"
    assert (seq.duration_frames, seq.audio_tracks, seq.video_tracks, seq.clip_count) == (
        60,
        2,
        2,
        2,
    )
    assert seq.duration_seconds == pytest.approx(2.002)
    assert len(summary.file.sha256) == 64


def test_ntsc_timeline_frame_accuracy():
    tl = project.timeline(load(KD26 / "kd26-ntsc-timeline.kdenlive"))
    assert [t.label for t in tl.tracks] == ["A2", "A1", "V1", "V2"]
    v1 = next(t for t in tl.tracks if t.label == "V1")
    (clip,) = v1.clips
    # 2 s at 30000/1001 fps = 60 frames: Kdenlive stored out="00:00:01.969" (inclusive frame 59).
    assert (clip.start, clip.end, clip.duration, clip.source_in, clip.source_out) == (
        0,
        60,
        60,
        0,
        59,
    )
    assert clip.resource == "media/ntsc.mp4"
    assert clip.name == "ntsc.mp4"
    assert clip.service == "avformat-novalidate"
    assert clip.bin_id is not None
    assert tl.duration_frames == 60
    assert "inclusive" in tl.conventions and not tl.truncated


def test_empty_timeline_is_not_missing_data():
    tl = project.timeline(load(KD26 / "kd26-ntsc.kdenlive"))
    assert tl.duration_frames == 0
    assert len(tl.tracks) == 4
    assert all(t.clips == [] for t in tl.tracks)
    assert tl.guides == [] and tl.compositions == []


# ------------------------------------------------- upstream projects, several versions --
@pytest.mark.parametrize("name", ["clip-ids", "test-keyframes", "test-nesting"])
def test_resave_by_kdenlive_26_keeps_timing(name):
    original = project.timeline(load(UP / f"{name}.kdenlive"))
    resaved = project.timeline(load(UP / f"{name}.resaved-26.08.2.kdenlive"))
    assert clips_of(original) == clips_of(resaved)


def test_gaps_and_multiple_tracks():
    tl = project.timeline(load(UP / "clip-ids.kdenlive"))
    starts = {t.label: t.clips[0].start for t in tl.tracks}
    # A blank of 00:00:00.640 (16 frames at 25 fps) precedes the first clip on some tracks.
    assert sorted(starts.values()) == [11, 11, 16, 16]
    assert tl.total_clips == 16
    for track in tl.tracks:
        ends = [c.end for c in track.clips]
        assert ends == sorted(ends)


def test_mixes_and_secondary_layer():
    doc = load(UP / "test-mix.kdenlive")
    assert doc.generation == 4
    tl = project.timeline(doc)
    v1 = next(t for t in tl.tracks if t.label == "V1")
    assert {c.layer for c in v1.clips} == {0, 1}
    assert [(m.kdenlive_id, m.start) for m in v1.mixes] == [("luma", 127), ("luma", 364)]
    assert all(m.cut_offset is not None for m in v1.mixes)


def test_clip_effects_are_listed():
    tl = project.timeline(load(UP / "test-keyframes.kdenlive"))
    v1 = next(t for t in tl.tracks if t.label == "V1")
    assert v1.clips[0].effects == ["brightness"]


def test_multiple_sequences_and_selection():
    doc = load(UP / "test-nesting.kdenlive")
    summary = project.summarize(doc)
    assert [s.name for s in summary.sequences] == ["Sequence 1", "Sequence 2", "Sequence 3"]
    assert sum(s.active for s in summary.sequences) == 1
    second = summary.sequences[1]
    by_name = project.timeline(doc, "Sequence 2")
    by_uuid = project.timeline(doc, second.uuid)
    assert by_name.sequence_uuid == by_uuid.sequence_uuid == second.uuid
    nested = [c for t in by_name.tracks for c in t.clips]
    assert all(c.service == "tractor" for c in nested)  # sequence clips nested in a sequence
    with pytest.raises(ProjectError) as err:
        project.timeline(doc, "nope")
    assert err.value.code == "sequence_not_found"


@pytest.mark.parametrize("path", sorted((FIX).glob("*/*.kdenlive")), ids=lambda p: p.name)
def test_every_fixture_is_readable(path):
    doc = load(path)
    summary = project.summarize(doc)
    tl = project.timeline(doc)
    assert summary.sequences and tl.duration_frames >= 0


# --------------------------------------------------------------- robustness ----------
def test_unknown_metadata_is_tolerated(tmp_path):
    text = (KD26 / "kd26-ntsc-timeline.kdenlive").read_text(encoding="utf-8")
    text = text.replace(
        '<playlist id="main_bin">',
        '<futureelement x="1"><child/></futureelement>\n<playlist id="main_bin">'
        '<property name="kdenlive:docproperties.futurething">42</property>',
        1,
    )
    text = text.replace(
        "</mlt>", '<!-- trailing comment --><vendor:thing xmlns:vendor="urn:x"/></mlt>'
    )
    copy = tmp_path / "unknown.kdenlive"
    copy.write_text(text, encoding="utf-8")
    assert clips_of(project.timeline(load(copy))) == clips_of(
        project.timeline(load(KD26 / "kd26-ntsc-timeline.kdenlive"))
    )


def test_inspection_never_writes(tmp_path):
    src = tmp_path / "p.kdenlive"
    shutil.copy(UP / "test-mix.kdenlive", src)
    before = (src.read_bytes(), src.stat().st_mtime_ns)
    project.summarize(load(src))
    project.timeline(load(src))
    assert (src.read_bytes(), src.stat().st_mtime_ns) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["p.kdenlive"]


def test_large_timeline_is_truncated(tmp_path):
    entries = "".join('<entry producer="chain0" in="0" out="0"/>' for _ in range(5000))
    text = (KD26 / "kd26-ntsc-timeline.kdenlive").read_text(encoding="utf-8")
    start = text.index('<playlist id="playlist4">')
    end = text.index("</playlist>", start)
    text = text[:start] + '<playlist id="playlist4">' + entries + text[end:]
    big = tmp_path / "big.kdenlive"
    big.write_text(text, encoding="utf-8")
    tl = project.timeline(load(big), max_clips=100)
    assert tl.truncated and tl.returned_clips == 100 and tl.total_clips >= 5000
    assert tl.duration_frames == 5000


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"", "invalid_xml"),
        (b"<mlt><unclosed></mlt>", "invalid_xml"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "not_a_project"),
        (
            b"<mlt><profile frame_rate_num='25' frame_rate_den='1'/><tractor id='t'/></mlt>",
            "not_a_project",
        ),
        (b"<!DOCTYPE mlt [<!ENTITY e 'x'>]><mlt>&e;</mlt>", "unsafe_xml"),
        (b"<mlt><playlist id='main_bin'/></mlt>", "unsupported_structure"),
        (b"\xff\xfe garbage", "invalid_xml"),
    ],
)
def test_invalid_inputs(tmp_path, content, code):
    bad = tmp_path / "bad.kdenlive"
    bad.write_bytes(content)
    with pytest.raises(ProjectError) as err:
        project.summarize(load(bad))
    assert err.value.code == code


def test_path_errors(tmp_path):
    cases = {
        "relative.kdenlive": "invalid_path",
        str(tmp_path / "nope.kdenlive"): "not_found",
        str(tmp_path): "not_a_file",
        "": "invalid_path",
    }
    for raw, code in cases.items():
        with pytest.raises(ProjectError) as err:
            kdenlive_doc.load(raw)
        assert err.value.code == code, raw


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_permission_denied(tmp_path):
    locked = tmp_path / "locked.kdenlive"
    shutil.copy(KD26 / "kd26-ntsc.kdenlive", locked)
    locked.chmod(0)
    try:
        with pytest.raises(ProjectError) as err:
            kdenlive_doc.load(str(locked))
        assert err.value.code == "permission_denied"
    finally:
        locked.chmod(0o644)


def test_size_limit_is_enforced():
    with pytest.raises(ProjectError) as err:
        kdenlive_doc.load(str((KD26 / "kd26-ntsc.kdenlive").resolve()), max_bytes=1000)
    assert err.value.code == "too_large"


def test_invalid_max_clips():
    with pytest.raises(ProjectError):
        project.timeline(load(KD26 / "kd26-ntsc.kdenlive"), max_clips=0)
