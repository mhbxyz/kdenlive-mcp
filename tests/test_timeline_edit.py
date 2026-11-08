"""Timeline operations (#11) on a Kdenlive 26.08.2 project at 29.97 fps."""

from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mcp_kdenlive import kdenlive_doc, project, writer
from mcp_kdenlive import timeline_edit as te
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import props

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def ws(tmp_path) -> Path:
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    return tmp_path / "p"


class Session:
    """Apply successive operations, each saved to a new file like a client would."""

    def __init__(self, ws: Path) -> None:
        self.ws = ws
        self.current = ws / "kd26-ntsc-timeline.kdenlive"
        self.n = 0

    def apply(self, fn, *args, **kwargs):
        edit = writer.open_for_edit(str(self.current))
        result = fn(edit, *args, **kwargs)
        self.n += 1
        dest = self.ws / f"step{self.n}.kdenlive"
        writer.save(edit, str(dest))
        self.current = dest
        return result

    def timeline(self) -> project.Timeline:
        return project.timeline(kdenlive_doc.load(str(self.current)))

    def rev(self) -> str:
        return self.timeline().file.sha256

    def clips(self) -> dict[str, list[tuple[int, int, int, int, str | None]]]:
        return {
            t.label: [(c.start, c.end, c.source_in, c.source_out, c.bin_id) for c in t.clips]
            for t in self.timeline().tracks
            if t.clips
        }

    def ref(self, label: str, start: int) -> str:
        return next(
            c.ref
            for t in self.timeline().tracks
            if t.label == label
            for c in t.clips
            if c.start == start
        )

    def groups(self) -> list[list[str]]:
        seq = kdenlive_doc.load(str(self.current)).sequence_tractors()[0]
        raw = props(seq).get("kdenlive:sequenceproperties.groups", "").strip()
        return sorted(
            sorted(leaf["data"] for leaf in g["children"]) for g in json.loads(raw or "[]")
        )


@pytest.fixture
def s(ws) -> Session:
    return Session(ws)


def test_insert_av_clip_with_audio_and_group(s):
    r = s.apply(te.insert_clip, "5", "V1", 60)
    assert [(c.track, c.start, c.end) for c in r.clips] == [("V1", 60, 120), ("A1", 60, 120)]
    assert s.clips()["V1"] == [(0, 60, 0, 59, "4"), (60, 120, 0, 59, "5")]
    assert ["1:60:-1", "2:60:-1"] in s.groups()
    assert r.sequence_duration_frames == 120


def test_insert_with_gap_and_subrange(s):
    s.apply(te.insert_clip, "6", "A2", 10, 5, 34)
    assert s.clips()["A2"] == [(10, 40, 5, 34, "6")]


def test_overlap_is_refused_and_nothing_written(s):
    with pytest.raises(ProjectError) as err:
        s.apply(te.insert_clip, "5", "V1", 30)
    assert err.value.code == "overlap" and s.n == 0


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (("99", "V1", 100), "clip_not_found"),
        (("5", "V9", 100), "track_not_found"),
        (("6", "V1", 100), "incompatible_track"),  # tone.wav has no video
        (("5", "V1", 100, 0, 999), "invalid_range"),
        (("5", "V1", 100, 30, 10), "invalid_range"),
    ],
)
def test_insert_errors(s, args, code):
    with pytest.raises(ProjectError) as err:
        s.apply(te.insert_clip, *args)
    assert err.value.code == code


def test_split_then_grouped_move(s):
    s.apply(te.insert_clip, "5", "V1", 60)
    s.apply(te.split_clip, s.ref("V1", 0), s.rev(), 30)
    assert s.clips()["V1"][:2] == [(0, 30, 0, 29, "4"), (30, 60, 30, 59, "4")]
    assert s.clips()["A1"][:2] == [(0, 30, 0, 29, "4"), (30, 60, 30, 59, "4")]
    assert ["1:30:-1", "2:30:-1"] in s.groups() and ["1:0:-1", "2:0:-1"] in s.groups()
    s.apply(te.move_clip, s.ref("V1", 30), s.rev(), 150)
    assert (150, 180, 30, 59, "4") in s.clips()["V1"] and (150, 180, 30, 59, "4") in s.clips()["A1"]
    assert ["1:150:-1", "2:150:-1"] in s.groups()
    assert s.clips()["V1"][1] == (60, 120, 0, 59, "5")  # neighbour untouched


def test_move_to_another_track_and_kind_check(s):
    s.apply(te.insert_clip, "5", "V1", 60, 0, None, None)  # video only, no group
    s.apply(te.move_clip, s.ref("V1", 60), s.rev(), 70, "V2")
    assert s.clips()["V2"] == [(70, 130, 0, 59, "5")]
    with pytest.raises(ProjectError) as err:
        s.apply(te.move_clip, s.ref("V2", 70), s.rev(), 0, "A2")
    assert err.value.code == "incompatible_track"


def test_trim_both_ends_follows_source(s):
    s.apply(te.insert_clip, "5", "V1", 60)
    s.apply(te.trim_clip, s.ref("V1", 60), s.rev(), 65, 110)
    assert s.clips()["V1"][1] == (65, 110, 5, 49, "5")
    assert s.clips()["A1"][1] == (65, 110, 5, 49, "5")
    with pytest.raises(ProjectError) as err:  # cannot extend past the source end (60 frames)
        s.apply(te.trim_clip, s.ref("V1", 65), s.rev(), None, 200)
    assert err.value.code == "invalid_range"
    with pytest.raises(ProjectError) as err:  # would overlap the first clip
        s.apply(te.trim_clip, s.ref("V1", 65), s.rev(), 55, None)
    assert err.value.code in ("overlap", "invalid_range")


def test_remove_leaves_a_gap(s):
    s.apply(te.insert_clip, "5", "V1", 60)
    s.apply(te.remove_clip, s.ref("V1", 0), s.rev())
    assert s.clips()["V1"] == [(60, 120, 0, 59, "5")]
    assert "A1" in s.clips() and s.clips()["A1"] == [(60, 120, 0, 59, "5")]
    assert ["1:0:-1", "2:0:-1"] not in s.groups()


def test_stale_and_missing_revision(s):
    ref = s.ref("V1", 0)
    old = s.rev()
    s.apply(te.insert_clip, "5", "V1", 60)
    with pytest.raises(ProjectError) as err:
        s.apply(te.remove_clip, ref, old)
    assert err.value.code == "stale_revision"
    with pytest.raises(ProjectError) as err:
        s.apply(te.remove_clip, ref, "")
    assert err.value.code == "revision_required"


@pytest.mark.parametrize("bad", ["x", "9:0:0", "3:1:0", "3:0:7"])
def test_bad_refs(s, bad):
    with pytest.raises(ProjectError):
        s.apply(te.remove_clip, bad, s.rev())


def test_split_bounds(s):
    for at in (0, 60, 61):
        with pytest.raises(ProjectError):
            s.apply(te.split_clip, s.ref("V1", 0), s.rev(), at)


def test_keyframed_effects_block_split_and_start_trim(s, ws):
    text = s.current.read_text(encoding="utf-8")
    text = text.replace(
        '<property name="kdenlive:id">4</property>\n  </entry>',
        '<property name="kdenlive:id">4</property><filter id="fk"><property '
        'name="mlt_service">brightness</property><property name="level">0=0.2;59=1'
        "</property></filter>\n  </entry>",
        1,
    )
    s.current.write_text(text, encoding="utf-8")
    ref = (
        s.ref("A1", 0)
        if "fk" in text.split("playlist2")[1].split("</playlist>")[0]
        else s.ref("V1", 0)
    )
    with pytest.raises(ProjectError) as err:
        s.apply(te.split_clip, ref, s.rev(), 30)
    assert err.value.code == "unsupported_structure"


def test_mixed_tracks_are_refused(tmp_path):
    shutil.copytree(FIX / "upstream", tmp_path / "u")
    src = tmp_path / "u" / "test-keyframes.resaved-26.08.2.kdenlive"
    edit = writer.open_for_edit(str(src))
    timeline = te.Timeline(edit, edit.doc.sequence_tractors()[0])
    # test-mix is generation 4; craft a mix on a 26.08 track instead
    track = timeline.tracks["V1"]
    mix = ET.SubElement(track.tractor, "transition", {"id": "mix0"})
    ET.SubElement(mix, "property", {"name": "mlt_service"}).text = "luma"
    with pytest.raises(ProjectError) as err:
        timeline.check_editable(track)
    assert err.value.code == "unsupported_structure"


def test_durations_follow_the_timeline(s):
    s.apply(te.insert_clip, "5", "V1", 200)
    seq = props(kdenlive_doc.load(str(s.current)).sequence_tractors()[0])
    assert seq["kdenlive:maxduration"] == "260"
    s.apply(te.remove_clip, s.ref("V1", 200), s.rev())
    assert (
        props(kdenlive_doc.load(str(s.current)).sequence_tractors()[0])["kdenlive:maxduration"]
        == "60"
    )


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("melt") is None, reason="melt is not installed")
def test_montage_renders_with_melt(s, tmp_path):
    import subprocess

    s.apply(te.insert_clip, "5", "V1", 60)
    s.apply(te.insert_clip, "6", "A2", 10, 0, 44)
    s.apply(te.split_clip, s.ref("V1", 0), s.rev(), 30)
    s.apply(te.move_clip, s.ref("V1", 30), s.rev(), 150)
    out = tmp_path / "m.mp4"
    done = subprocess.run(
        ["melt", "-quiet", str(s.current), "-consumer", f"avformat:{out}"],
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0
    if shutil.which("ffprobe"):
        frames = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "csv=p=0",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).stdout.strip()
        assert frames == "180"
