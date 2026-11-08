"""Editing tracks that contain same-track mixes (crossfades), #61.

Fixture: tests/fixtures/kd26/kd26-mixes.kdenlive. Three AV clips of ntsc.mp4 were placed at
[0,40) [40,80) [80,130) by this package, then real Kdenlive 26.08.2 added the crossfades (Select
All, Mix Clips). Kdenlive extended the clips into their handles:

    V1/A1 layer 0: clip1 [0,60) src 0..59          clip3 [70,130) src 0..59
    V1/A1 layer 1:          clip2 [30,90) src 0..59
    mix1 [30,60) cut 40 (mixcut 20)   mix2 [70,90) cut 80 (mixcut 10)
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from test_timeline_edit import Session

from mcp_kdenlive import kdenlive_doc, project, writer
from mcp_kdenlive import timeline_edit as te
from mcp_kdenlive.errors import ProjectError

FIX = Path(__file__).parent / "fixtures"

BASE = {
    "V1": [(0, 60, 0, 59, "4"), (30, 90, 0, 59, "4"), (70, 130, 0, 59, "4")],
    "A1": [(0, 60, 0, 59, "4"), (30, 90, 0, 59, "4"), (70, 130, 0, 59, "4")],
}
MIXES = [(30, 60, 40), (70, 90, 80)]


@pytest.fixture
def m(tmp_path) -> Session:
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    session = Session(tmp_path / "p")
    session.current = tmp_path / "p" / "kd26-mixes.kdenlive"
    return session


def mixes(s: Session) -> dict[str, list[tuple[int | None, int | None, int | None]]]:
    return {
        t.label: [(x.start, x.end, x.cut) for x in t.mixes] for t in s.timeline().tracks if t.mixes
    }


def layers(s: Session, label: str) -> list[tuple[int, int]]:
    track = next(t for t in s.timeline().tracks if t.label == label)
    return [(c.start, c.layer) for c in track.clips]


def code(s: Session, fn, *args, **kwargs) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(ProjectError) as err:
        s.apply(fn, *args, **kwargs)
    return err.value.code


# ------------------------------------------------------------------ reading -----------
def test_reader_reports_kdenlive_mix_bounds_and_cut(m):
    assert m.clips() == BASE
    assert mixes(m) == {"V1": MIXES, "A1": MIXES}
    assert layers(m, "V1") == [(0, 0), (30, 1), (70, 0)]


# ------------------------------------------------------------------ untouched mixes ---
def test_insert_and_move_around_mixes(m):
    m.apply(te.insert_clip, "4", "V1", 140, 0, 19)
    assert mixes(m) == {"V1": MIXES, "A1": MIXES}
    m.apply(te.move_clip, m.ref("V1", 140), m.rev(), 150)
    assert m.clips()["V1"][-1] == (150, 170, 0, 19, "4")
    assert m.clips()["A1"][-1] == (150, 170, 0, 19, "4")
    assert mixes(m) == {"V1": MIXES, "A1": MIXES}
    assert layers(m, "V1")[-1] == (150, 0)


def test_insert_over_a_mixed_clip_on_the_other_layer_is_an_overlap(m):
    # clip2 lives on layer 1 over [30,90); layer 0 is empty at 60..70 but clip2 is not.
    assert code(m, te.insert_clip, "4", "V1", 60, 0, 9, None) == "overlap"


def test_moving_a_mixed_clip_is_refused_with_the_mix(m):
    with pytest.raises(ProjectError) as err:
        m.apply(te.move_clip, m.ref("V1", 70), m.rev(), 200)
    assert err.value.code == "unsupported_structure"
    assert "mix" in err.value.message and "70" in err.value.message


# ------------------------------------------------------------------ trims -------------
def test_trim_the_free_side_of_mixed_clips(m):
    m.apply(te.trim_clip, m.ref("V1", 0), m.rev(), start=10)
    m.apply(te.trim_clip, m.ref("V1", 70), m.rev(), end=120)
    expected = [(10, 60, 10, 59, "4"), (30, 90, 0, 59, "4"), (70, 120, 0, 49, "4")]
    assert m.clips() == {"V1": expected, "A1": expected}
    assert mixes(m) == {"V1": MIXES, "A1": MIXES}
    assert sorted(m.groups()) == [
        ["1:10:-1", "2:10:-1"],
        ["1:30:-1", "2:30:-1"],
        ["1:70:-1", "2:70:-1"],
    ]


@pytest.mark.parametrize(
    ("start", "kwargs"),
    [
        (0, {"end": 55}),
        (70, {"start": 75}),
        (30, {"start": 35}),
        (30, {"end": 85}),
        (0, {"start": 35}),
    ],
)
def test_trims_that_would_break_a_mix_are_refused(m, start, kwargs):
    assert code(m, te.trim_clip, m.ref("V1", start), m.rev(), **kwargs) in {
        "unsupported_structure",
        "invalid_range",
    }
    assert m.clips() == BASE


# ------------------------------------------------------------------ splits ------------
def test_split_outside_the_mixes(m):
    m.apply(te.split_clip, m.ref("V1", 70), m.rev(), 110)
    assert m.clips()["V1"] == [*BASE["V1"][:2], (70, 110, 0, 39, "4"), (110, 130, 40, 59, "4")]
    assert mixes(m) == {"V1": MIXES, "A1": MIXES}
    assert layers(m, "V1")[-1] == (110, 0)
    # the middle clip has a mix at each end: both halves keep theirs, on layer 1
    m.apply(te.split_clip, m.ref("V1", 30), m.rev(), 65)
    assert m.clips()["A1"][1:3] == [(30, 65, 0, 34, "4"), (65, 90, 35, 59, "4")]
    assert layers(m, "V1")[1:3] == [(30, 1), (65, 1)]
    assert mixes(m) == {"V1": MIXES, "A1": MIXES}


def test_split_inside_a_mix_is_refused(m):
    assert code(m, te.split_clip, m.ref("V1", 70), m.rev(), 80) == "invalid_range"


# ------------------------------------------------------------------ removals ----------
def test_removing_the_middle_clip_restores_the_cuts(m):
    m.apply(te.remove_clip, m.ref("V1", 30), m.rev())
    expected = [(0, 40, 0, 39, "4"), (80, 130, 10, 59, "4")]
    assert m.clips() == {"V1": expected, "A1": expected}
    assert mixes(m) == {}
    assert layers(m, "V1") == [(0, 0), (80, 0)]
    assert sorted(m.groups()) == [["1:0:-1", "2:0:-1"], ["1:80:-1", "2:80:-1"]]


def test_removing_a_clip_with_one_mix_keeps_the_other(m):
    m.apply(te.remove_clip, m.ref("V1", 70), m.rev())
    expected = [(0, 60, 0, 59, "4"), (30, 80, 0, 49, "4")]
    assert m.clips() == {"V1": expected, "A1": expected}
    assert mixes(m) == {"V1": [MIXES[0]], "A1": [MIXES[0]]}
    assert layers(m, "V1") == [(0, 0), (30, 1)]


def test_removing_the_first_clip(m):
    m.apply(te.remove_clip, m.ref("V1", 0), m.rev())
    expected = [(40, 90, 10, 59, "4"), (70, 130, 0, 59, "4")]
    assert m.clips() == {"V1": expected, "A1": expected}
    assert mixes(m) == {"V1": [MIXES[1]], "A1": [MIXES[1]]}
    assert sorted(m.groups()) == [["1:40:-1", "2:40:-1"], ["1:70:-1", "2:70:-1"]]


# ------------------------------------------------------------------ structure ---------
def test_unknown_same_track_transition_is_refused(m, tmp_path):
    edit = writer.open_for_edit(str(m.current))
    tl = te.Timeline(edit, edit.doc.sequence_tractors()[0])
    odd = ET.SubElement(tl.tracks["V1"].tractor, "transition", {"in": "00:00:05.000"})
    ET.SubElement(odd, "property", {"name": "mlt_service"}).text = "composite"
    writer.save(edit, str(tmp_path / "odd.kdenlive"))
    m.current = tmp_path / "odd.kdenlive"
    assert code(m, te.insert_clip, "4", "V1", 140, 0, 9, None) == "unsupported_structure"


def test_mix_ends_where_the_left_clip_ends_in_the_upstream_fixture():
    doc = kdenlive_doc.load(str((FIX / "upstream" / "test-mix.kdenlive").resolve()))
    v1 = next(t for t in project.timeline(doc).tracks if t.label == "V1")
    ends = {c.end for c in v1.clips}
    assert all(mix.end in ends for mix in v1.mixes)
