"""Clip audio level and fades (#13), written like Kdenlive 26.08.2 does.

Format observed in the GUI lab (Kdenlive's own addEffect on timeline clips, then Save As):

    <filter><property name="mlt_service">volume</property>
            <property name="kdenlive_id">volume</property><property name="level">0=0</property>…
    <filter in="<source in>" out="<source in + N>">  kdenlive_id=fadein,  gain=0 end=1
    <filter in="<source out - N>" out="<source out>"> kdenlive_id=fadeout, gain=1 end=0

Filter positions are source frames of the clip instance (verified on a clip using source frames
15..44: Kdenlive wrote the fade-in from 15).
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from test_timeline_edit import Session

from mcp_kdenlive import audio, kdenlive_doc, writer
from mcp_kdenlive import timeline_edit as te
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import props
from mcp_kdenlive.timecode import parse_time

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def s(tmp_path) -> Session:
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    return Session(tmp_path / "p")  # kd26-ntsc-timeline: one AV clip [0,60) on V1 + A1


def info(s: Session, label: str, index: int = 0):  # type: ignore[no-untyped-def]
    track = next(t for t in s.timeline().tracks if t.label == label)
    return track.clips[index]


def filters(s: Session, label: str, index: int = 0) -> list[dict[str, str | None]]:
    edit = writer.open_for_edit(str(s.current))
    tl = te.Timeline(edit, edit.doc.sequence_tractors()[0])
    _, item = tl.resolve_ref(info(s, label, index).ref)
    return [
        {"in": f.get("in"), "out": f.get("out"), **props(f)} for f in item.element.findall("filter")
    ]


def code(s: Session, fn, *args, **kwargs) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(ProjectError) as err:
        s.apply(fn, *args, **kwargs)
    return err.value.code


# ------------------------------------------------------------------ level --------------
def test_volume_goes_on_the_audio_part_of_an_av_clip(s):
    r = s.apply(audio.set_clip_volume, s.ref("V1", 0), s.rev(), -6.0)
    assert [(c.track, c.level_db) for c in r.clips] == [("A1", -6.0)]
    (flt,) = filters(s, "A1")
    assert (flt["mlt_service"], flt["kdenlive_id"], flt["level"]) == ("volume", "volume", "0=-6")
    assert flt["max_gain"] == "20dB"
    assert filters(s, "V1") == []
    assert info(s, "A1").audio.level_db == -6.0
    # setting it again updates the same effect
    s.apply(audio.set_clip_volume, s.ref("A1", 0), s.rev(), 3.5)
    assert [f["level"] for f in filters(s, "A1")] == ["0=3.5"]


@pytest.mark.parametrize("level", [-100.5, 20.5, float("nan")])
def test_volume_range(s, level):
    assert code(s, audio.set_clip_volume, s.ref("A1", 0), s.rev(), level) == "invalid_argument"


def test_video_only_clip_has_no_audio(s):
    s.apply(te.insert_clip, "4", "V2", 0, 0, 29, None)
    assert code(s, audio.set_clip_volume, s.ref("V2", 0), s.rev(), -3.0) == "incompatible_track"


def test_animated_volume_is_not_replaced(s, tmp_path):
    s.apply(audio.set_clip_volume, s.ref("A1", 0), s.rev(), -6.0)
    text = s.current.read_text(encoding="utf-8").replace(">0=-6<", ">0=-6;30=-20<")
    animated = tmp_path / "animated.kdenlive"
    animated.write_text(text, encoding="utf-8")
    s.current = animated
    assert code(s, audio.set_clip_volume, s.ref("A1", 0), s.rev(), 0.0) == "unsupported_structure"


# ------------------------------------------------------------------ fades --------------
def test_fades_are_written_in_source_frames(s):
    s.apply(te.trim_clip, s.ref("V1", 0), s.rev(), start=10)  # source 10..59 now
    r = s.apply(audio.set_clip_fades, s.ref("A1", 10), s.rev(), fade_in=15, fade_out=20)
    assert [(c.fade_in, c.fade_out) for c in r.clips] == [(15, 20)]
    fade_in, fade_out = filters(s, "A1")
    assert (fade_in["kdenlive_id"], fade_in["gain"], fade_in["end"]) == ("fadein", "0", "1")
    rate = kdenlive_doc.load(str(s.current)).rate
    assert parse_time(fade_in["in"], rate) == 10
    assert parse_time(fade_in["out"], rate) == 25
    assert (fade_out["kdenlive_id"], fade_out["gain"], fade_out["end"]) == ("fadeout", "1", "0")
    assert parse_time(fade_out["in"], rate) == 39
    assert parse_time(fade_out["out"], rate) == 59
    a1 = info(s, "A1").audio
    assert (a1.fade_in, a1.fade_out, a1.level_db) == (15, 20, None)


def test_fades_update_and_remove(s):
    s.apply(audio.set_clip_fades, s.ref("A1", 0), s.rev(), fade_in=10, fade_out=10)
    s.apply(audio.set_clip_fades, s.ref("A1", 0), s.rev(), fade_in=30)
    assert (info(s, "A1").audio.fade_in, info(s, "A1").audio.fade_out) == (30, 10)
    s.apply(audio.set_clip_fades, s.ref("A1", 0), s.rev(), fade_in=0, fade_out=0)
    assert filters(s, "A1") == []
    assert info(s, "A1").audio is None


@pytest.mark.parametrize(("fade_in", "fade_out"), [(40, 30), (-1, None), (None, 61)])
def test_fades_must_fit_the_clip(s, fade_in, fade_out):
    assert code(
        s, audio.set_clip_fades, s.ref("A1", 0), s.rev(), fade_in=fade_in, fade_out=fade_out
    ) in {"invalid_range", "invalid_argument"}


def test_fades_follow_trims_and_splits(s):
    s.apply(audio.set_clip_volume, s.ref("A1", 0), s.rev(), -3.0)
    s.apply(audio.set_clip_fades, s.ref("A1", 0), s.rev(), fade_in=10, fade_out=12)
    # a static level does not count as animated: trimming the start is allowed
    s.apply(te.trim_clip, s.ref("V1", 0), s.rev(), start=5, end=55)
    a1 = info(s, "A1")
    assert (a1.source_in, a1.source_out, a1.audio.fade_in, a1.audio.fade_out) == (5, 54, 10, 12)
    rate = kdenlive_doc.load(str(s.current)).rate
    ins = {f["kdenlive_id"]: f for f in filters(s, "A1")}
    assert parse_time(ins["fadein"]["in"], rate) == 5
    assert parse_time(ins["fadeout"]["out"], rate) == 54
    s.apply(te.split_clip, s.ref("V1", 5), s.rev(), 30)
    left, right = info(s, "A1", 0).audio, info(s, "A1", 1).audio
    assert (left.fade_in, left.fade_out, left.level_db) == (10, 0, -3.0)
    assert (right.fade_in, right.fade_out, right.level_db) == (0, 12, -3.0)


def test_unrelated_effects_are_kept(s, tmp_path):
    edit = writer.open_for_edit(str(s.current))
    tl = te.Timeline(edit, edit.doc.sequence_tractors()[0])
    _, item = tl.resolve_ref(s.ref("A1", 0))
    other = ET.SubElement(item.element, "filter", {"id": "filter99"})
    ET.SubElement(other, "property", {"name": "mlt_service"}).text = "panner"
    ET.SubElement(other, "property", {"name": "kdenlive_id"}).text = "pan"
    writer.save(edit, str(tmp_path / "pan.kdenlive"))
    s.current = tmp_path / "pan.kdenlive"
    s.apply(audio.set_clip_fades, s.ref("A1", 0), s.rev(), fade_in=5)
    s.apply(audio.set_clip_volume, s.ref("A1", 0), s.rev(), -1.0)
    assert [f["kdenlive_id"] for f in filters(s, "A1")] == ["pan", "fadein", "volume"]
