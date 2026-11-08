"""Subtitles (#16): read Kdenlive's subtitle track, import SRT/WebVTT files.

Kdenlive 26.08.2 keeps the main sequence's subtitles in `<project file>.ass` next to the project
(tests/fixtures/kd26/kd26-subtitle.kdenlive.ass, written by Kdenlive), referenced by an internal
`avfilter.subtitles` filter on the sequence tractor (`av.filename`, relative) and by
`kdenlive:sequenceproperties.subtitlesList` ([{"file": <absolute>, "id": 0, "name": ...}]).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_timeline_edit import Session

from mcp_kdenlive import kdenlive_doc, subtitles, writer
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import props

FIX = Path(__file__).parent / "fixtures"

SRT = (
    "1\r\n00:00:01,000 --> 00:00:03,500\r\nBonjour à tous\r\nDeuxième ligne — 東京\r\n\r\n"
    "2\r\n00:00:03,000 --> 00:00:05,000\r\nChevauchement\r\n\r\n"
    "3\r\n00:00:06,040 --> 00:00:07,000\r\n<i>Italique</i> et <b>gras</b>\r\n"
)


@pytest.fixture
def ws(tmp_path) -> Path:
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    return tmp_path / "p"


@pytest.fixture
def s(ws) -> Session:
    return Session(ws)  # kd26-ntsc-timeline: 29.97 fps, 1280x720, no subtitles


def write(ws: Path, name: str, text: str | bytes) -> Path:
    path = ws / name
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8", newline="")
    return path


def report(path: Path) -> subtitles.SubtitleReport:
    return subtitles.list_subtitles(kdenlive_doc.load(str(path)))


def code(s: Session, *args, **kwargs) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(ProjectError) as err:
        s.apply(subtitles.import_subtitles, *args, **kwargs)
    return err.value.code


# ------------------------------------------------------------------ reading ------------
def test_reads_kdenlives_own_subtitles(ws):
    r = report(ws / "kd26-subtitle.kdenlive")
    (track,) = r.tracks
    assert (track.id, track.name, track.status) == (0, "Subtitles", "ok")
    assert track.file.endswith("kd26-subtitle.kdenlive.ass")
    (event,) = track.events
    assert (event.start, event.end, event.text, event.layer) == (60, 210, "Bonjour é ✓", 0)
    assert event.start_timecode == "00:00:02:00"
    assert track.styles == ["Default"]


def test_project_without_subtitles(ws):
    assert report(ws / "kd26-ntsc-timeline.kdenlive").tracks == []


def test_missing_subtitle_file_is_reported(ws):
    (ws / "kd26-subtitle.kdenlive.ass").unlink()
    (track,) = report(ws / "kd26-subtitle.kdenlive").tracks
    assert track.status == "missing" and track.events == []


# ------------------------------------------------------------------ import -------------
def test_import_srt_in_kdenlive_format(s, ws):
    srt = write(ws, "in.srt", SRT)
    r = s.apply(subtitles.import_subtitles, str(srt))
    assert (r.events, r.layers, r.format) == (3, 2, "srt")
    ass = ws / "step1.kdenlive.ass"  # named after the saved project, like Kdenlive
    text = ass.read_text(encoding="utf-8")
    assert "PlayResX: 1280" in text and "PlayResY: 720" in text
    assert "Style: Default,Arial,40.00," in text  # Kdenlive's default style
    assert "MaxLayer: 1" in text  # the overlapping cue went to layer 1
    assert "Dialogue: 0,00:00:01.00,00:00:03.50,Default,,0,0,0,,Bonjour à tous\\NDeuxième" in text
    assert "Dialogue: 1,00:00:03.00,00:00:05.01,Default,,0,0,0,,Chevauchement" in text
    assert "{\\i1}Italique{\\i0} et {\\b1}gras{\\b0}" in text
    seq = s.timeline()
    doc = kdenlive_doc.load(str(s.current))
    tractor = next(
        t for t in doc.sequence_tractors() if props(t).get("kdenlive:uuid") == seq.sequence_uuid
    )
    (flt,) = [
        f for f in tractor.findall("filter") if props(f).get("mlt_service") == "avfilter.subtitles"
    ]
    assert props(flt) == {
        "mlt_service": "avfilter.subtitles", "av.alpha": "1", "internal_added": "237",
        "av.filename": "step1.kdenlive.ass",
    }  # fmt: skip
    listed = json.loads(props(tractor)["kdenlive:sequenceproperties.subtitlesList"])
    assert listed == [{"file": str(ass), "id": 0, "name": "Subtitles"}]
    events = report(s.current).tracks[0].events
    assert [(e.start, e.end, e.layer) for e in events] == [
        (30, 105, 0),
        (90, 150, 1),
        (181, 210, 0),
    ]
    assert events[0].text == "Bonjour à tous\nDeuxième ligne — 東京"


def test_frames_are_snapped_like_kdenlive(s, ws):
    """Times become whole frames (29.97 fps), then centiseconds in the ASS file."""
    srt = write(ws, "in.srt", "1\n00:00:00,020 --> 00:00:00,040\nx\n")
    s.apply(subtitles.import_subtitles, str(srt))
    (event,) = report(s.current).tracks[0].events
    assert (event.start, event.end) == (1, 2)  # 0.6 and 1.2 frames, at least one frame long


def test_webvtt(s, ws):
    vtt = write(
        ws, "in.vtt",
        "WEBVTT\n\nNOTE a comment\n\nintro\n00:01.000 --> 00:02.000 align:start\n"
        "<v Ana>Salut</v> <c.yellow>toi</c>\n\n01:00:00.000 --> 01:00:01.000\nUne heure\n",
    )  # fmt: skip
    r = s.apply(subtitles.import_subtitles, str(vtt))
    assert r.format == "vtt" and any("cue settings" in w for w in r.warnings)
    events = report(s.current).tracks[0].events
    assert [e.text for e in events] == ["Salut toi", "Une heure"]
    assert events[1].start == round(3600 * 30000 / 1001)


@pytest.mark.parametrize("bom", ["utf-8-sig", "utf-16"])
def test_unicode_encodings_with_bom(s, ws, bom):
    srt = write(ws, "in.srt", SRT.encode(bom))
    s.apply(subtitles.import_subtitles, str(srt))
    assert report(s.current).tracks[0].events[0].text.startswith("Bonjour à tous")


def test_offset(s, ws):
    srt = write(ws, "in.srt", "1\n00:00:01,000 --> 00:00:02,000\nx\n")
    s.apply(subtitles.import_subtitles, str(srt), offset=30)
    assert report(s.current).tracks[0].events[0].start == 60


def test_braces_are_not_hidden(s, ws):
    srt = write(ws, "in.srt", "1\n00:00:01,000 --> 00:00:02,000\na {b} c\n")
    r = s.apply(subtitles.import_subtitles, str(srt))
    assert report(s.current).tracks[0].events[0].text == "a (b) c"
    assert any("{" in w for w in r.warnings)


# ------------------------------------------------------------------ refusals -----------
@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("empty.srt", "", "invalid_argument"),
        ("blank.srt", "\n\n  \n", "invalid_argument"),
        (
            "latin1.srt",
            "1\n00:00:01,000 --> 00:00:02,000\nÉté\n".encode("latin-1"),
            "invalid_argument",
        ),
        ("bad-time.srt", "1\n00:00:01,000 -> 00:00:02,000\nx\n", "invalid_argument"),
        ("backwards.srt", "1\n00:00:03,000 --> 00:00:02,000\nx\n", "invalid_argument"),
        ("huge.srt", "1\n100:00:01,000 --> 100:00:02,000\nx\n", "invalid_range"),
        ("styles.ass", "[Script Info]\n", "invalid_argument"),
        ("novtt.vtt", "00:01.000 --> 00:02.000\nx\n", "invalid_argument"),
    ],
)
def test_malformed_or_unsupported_inputs(s, ws, name, content, expected):
    assert code(s, str(write(ws, name, content))) == expected


def test_negative_times_after_offset(s, ws):
    srt = write(ws, "in.srt", "1\n00:00:01,000 --> 00:00:02,000\nx\n")
    assert code(s, str(srt), offset=-60) == "invalid_range"


def test_existing_subtitles_are_not_replaced_silently(ws):
    s = Session(ws)
    s.current = ws / "kd26-subtitle.kdenlive"
    before = (ws / "kd26-subtitle.kdenlive.ass").read_bytes()
    srt = write(ws, "in.srt", SRT)
    assert code(s, str(srt)) == "subtitles_exist"
    r = s.apply(subtitles.import_subtitles, str(srt), replace=True)
    assert r.replaced_events == 1
    assert (ws / "kd26-subtitle.kdenlive.ass").read_bytes() == before  # the source's file is kept
    assert report(s.current).tracks[0].events[0].text.startswith("Bonjour à tous")
    assert len(report(s.current).tracks) == 1


def test_existing_ass_file_next_to_the_destination_is_not_overwritten(s, ws):
    write(ws, "step1.kdenlive.ass", "someone else's file")
    srt = write(ws, "in.srt", SRT)
    assert code(s, str(srt)) == "output_exists"
    assert (ws / "step1.kdenlive.ass").read_text() == "someone else's file"


def test_unrelated_project_data_is_kept(s, ws):
    before = s.timeline()
    s.apply(subtitles.import_subtitles, str(write(ws, "in.srt", SRT)))
    after = s.timeline()
    assert [(t.label, [(c.start, c.end) for c in t.clips]) for t in after.tracks] == [
        (t.label, [(c.start, c.end) for c in t.clips]) for t in before.tracks
    ]


def test_save_writes_the_subtitle_file_with_the_project(ws, tmp_path):
    edit = writer.open_for_edit(str(ws / "kd26-ntsc-timeline.kdenlive"))
    subtitles.import_subtitles(edit, str(write(ws, "in.srt", SRT)))
    out = tmp_path / "elsewhere" / "copy é.kdenlive"
    out.parent.mkdir()
    saved = writer.save(edit, str(out))
    assert saved.side_files == [str(out.parent / "copy é.kdenlive.ass")]
    assert (out.parent / "copy é.kdenlive.ass").is_file()
    assert report(out).tracks[0].status == "ok"
