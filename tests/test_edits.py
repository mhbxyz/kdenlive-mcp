"""Bin media and track edits (#10), checked structurally and against Kdenlive's conventions."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mcp_kdenlive import edits, kdenlive_doc, media, project, writer
from mcp_kdenlive.kdenlive_doc import ProjectError, props

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def ws(tmp_path) -> Path:
    shutil.copytree(FIX / "kd26", tmp_path / "proj")
    shutil.copy(FIX / "upstream" / "red.mp4", tmp_path / "proj" / "media" / "red.mp4")
    return tmp_path / "proj"


def tl_of(path: Path) -> project.Timeline:
    return project.timeline(kdenlive_doc.load(str(path)))


def internal(path: Path) -> list[tuple[str, str]]:
    doc = kdenlive_doc.load(str(path))
    tl = project.timeline(doc)
    label_of = {t.index: t.label for t in tl.tracks}
    seq = doc.sequence_tractors()[0]
    return sorted(
        (label_of[int(p["b_track"])], p["mlt_service"])
        for p in (props(t) for t in seq.findall("transition"))
        if p.get("internal_added")
    )


def test_add_video_track_on_top(ws):
    e = writer.open_for_edit(str(ws / "kd26-ntsc-timeline.kdenlive"))
    added = edits.add_track(e, "video", "Titres é")
    writer.save(e, str(ws / "out.kdenlive"))
    tl = tl_of(ws / "out.kdenlive")
    assert (added.label, added.index) == ("V3", 5)
    assert [(t.label, t.name) for t in tl.tracks][-1] == ("V3", "Titres é")
    assert internal(ws / "out.kdenlive") == [
        ("A1", "mix"),
        ("A2", "mix"),
        ("V1", "qtblend"),
        ("V2", "qtblend"),
        ("V3", "qtblend"),
    ]
    seq = props(kdenlive_doc.load(str(ws / "out.kdenlive")).sequence_tractors()[0])
    assert seq["kdenlive:sequenceproperties.tracksCount"] == "5"


def test_add_audio_track_at_bottom_shifts_indexes(ws):
    src = ws / "kd26-ntsc-timeline.kdenlive"
    before = tl_of(src)
    e = writer.open_for_edit(str(src))
    added = edits.add_track(e, "audio", "Music")
    writer.save(e, str(ws / "out.kdenlive"))
    after = tl_of(ws / "out.kdenlive")
    assert (added.label, added.index) == ("A3", 1)
    assert [t.label for t in after.tracks] == ["A3", "A2", "A1", "V1", "V2"]
    # clips keep their track (by label) and timing; only MLT indexes moved by one
    old = {t.label: [(c.start, c.end, c.bin_id) for c in t.clips] for t in before.tracks}
    new = {t.label: [(c.start, c.end, c.bin_id) for c in t.clips] for t in after.tracks}
    assert all(new[label] == clips for label, clips in old.items())
    assert internal(ws / "out.kdenlive") == [
        ("A1", "mix"),
        ("A2", "mix"),
        ("A3", "mix"),
        ("V1", "qtblend"),
        ("V2", "qtblend"),
    ]
    seq = props(kdenlive_doc.load(str(ws / "out.kdenlive")).sequence_tractors()[0])
    groups = json.loads(seq["kdenlive:sequenceproperties.groups"])
    assert sorted(leaf["data"] for leaf in groups[0]["children"]) == ["2:0:-1", "3:0:-1"]
    assert (
        seq["kdenlive:sequenceproperties.audioTarget"],
        seq["kdenlive:sequenceproperties.videoTarget"],
        seq["kdenlive:sequenceproperties.activeTrack"],
    ) == ("2", "3", "3")


def test_user_composition_follows_its_tracks(ws):
    src = ws / "kd26-ntsc-timeline.kdenlive"
    text = src.read_text(encoding="utf-8").replace(
        '<property name="kdenlive:sequenceproperties.hasAudio">',
        '<property name="kdenlive:sequenceproperties.hasAudio">',
        1,
    )
    # add a user composition V2 over V1 (MLT indexes 4 over 3)
    text = text.replace(
        '</tractor>\n <chain id="chain2"',
        '<transition id="usercomp" in="00:00:00.000" '
        'out="00:00:01.000"><property name="a_track">3</property><property '
        'name="b_track">4</property><property name="mlt_service">qtblend'
        '</property><property name="kdenlive_id">qtblend</property></transition>'
        '</tractor>\n <chain id="chain2"',
        1,
    )
    src.write_text(text, encoding="utf-8")
    assert [(c.a_track, c.b_track) for c in tl_of(src).compositions] == [(3, 4)]
    e = writer.open_for_edit(str(src))
    edits.add_track(e, "audio")
    writer.save(e, str(ws / "out.kdenlive"))
    assert [(c.a_track, c.b_track) for c in tl_of(ws / "out.kdenlive").compositions] == [(4, 5)]


@pytest.mark.parametrize(
    ("kind", "name"), [("subtitle", None), ("video", "x" * 300), ("audio", "bad\nname")]
)
def test_invalid_track_requests(ws, kind, name):
    with pytest.raises(ProjectError):
        edits.add_track(writer.open_for_edit(str(ws / "kd26-ntsc.kdenlive")), kind, name)


def test_add_media_with_stub_melt(stub_melt, ws):
    _env, args_file = stub_melt
    e = writer.open_for_edit(str(ws / "kd26-ntsc-timeline.kdenlive"))
    results = edits.add_media(
        e,
        [
            str(ws / "media" / "red.mp4"),
            str(ws / "media" / "ntsc.mp4"),
            str(ws / "media" / "red.mp4"),
        ],
    )
    assert [(r.status, r.bin_id) for r in results] == [
        ("added", "7"),
        ("existing", "4"),
        ("existing", "7"),
    ]
    assert results[0].reference == "media/red.mp4"
    args = args_file.read_text().splitlines()
    assert args[args.index("-profile") + 1].endswith(
        "project.profile"
    )  # project profile, not a name
    writer.save(e, str(ws / "out.kdenlive"))
    doc = kdenlive_doc.load(str(ws / "out.kdenlive"))
    rep = media.media_report(doc)
    assert rep.missing == [] and sorted(i.reference for i in rep.items if i.kind == "file") == [
        "media/ntsc.mp4",
        "media/pal clip é.mp4",
        "media/red.mp4",
        "media/tone.wav",
    ]
    assert project.timeline(doc).total_clips == 2  # bin only: the timeline is unchanged


def test_added_media_survive_a_move(stub_melt, ws, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    e = writer.open_for_edit(str(ws / "kd26-ntsc.kdenlive"))
    edits.add_media(e, [str(ws / "media" / "red.mp4")])
    result = writer.save(e, str(other / "moved.kdenlive"))
    assert "resource: media/red.mp4 -> ../proj/media/red.mp4" in result.rebased_paths
    assert media.media_report(kdenlive_doc.load(str(other / "moved.kdenlive"))).missing == []


def test_add_media_errors(stub_melt, ws):
    e = writer.open_for_edit(str(ws / "kd26-ntsc.kdenlive"))
    for bad in ([], [str(ws / "media" / "nope.mp4")], ["relative.mp4"], ["https://x/y.mp4"]):
        with pytest.raises(ProjectError):
            edits.add_media(e, bad)
    with pytest.raises(ProjectError):
        edits.add_media(e, [str(ws / "media" / "red.mp4")] * 51)


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("melt") is None, reason="melt is not installed")
def test_real_add_media_and_tracks_render_with_melt(ws, tmp_path):
    e = writer.open_for_edit(str(ws / "kd26-ntsc-timeline.kdenlive"))
    added = edits.add_media(e, [str(ws / "media" / "red.mp4")])
    assert added[0].duration_frames and added[0].duration_frames > 0
    edits.add_track(e, "audio", "Music")
    edits.add_track(e, "video", "Overlay")
    writer.save(e, str(ws / "out.kdenlive"))
    import subprocess

    done = subprocess.run(
        ["melt", "-quiet", str(ws / "out.kdenlive"), "-consumer", f"avformat:{tmp_path / 'o.mp4'}"],
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0 and (tmp_path / "o.mp4").stat().st_size > 0


def test_too_many_tracks(ws, monkeypatch):
    monkeypatch.setattr(edits, "MAX_TRACKS", 4)
    edit = writer.open_for_edit(str(ws / "kd26-ntsc.kdenlive"))  # 4 tracks already
    with pytest.raises(ProjectError) as err:
        edits.add_track(edit, "video")
    assert err.value.code == "too_many_tracks"
