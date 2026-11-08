"""Timeline guides and clip markers (#12), in Kdenlive 26.08.2's format.

Observed in the GUI lab (Kdenlive's *Add Clip Marker*, then Save As): clip markers are a JSON
list in the **bin clip**'s ``kdenlive:markers`` property, guides the same list in the sequence's
``kdenlive:sequenceproperties.guides``::

    [{"comment": "Marker", "duration": 0, "pos": 60, "type": 0}]

``pos`` and ``duration`` are frames (source frames for clip markers, timeline frames for guides);
``type`` is a category index from ``kdenlive:docproperties.guidesCategories``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_timeline_edit import Session

from mcp_kdenlive import kdenlive_doc, markers, writer
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import props

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def s(tmp_path) -> Session:
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    return Session(tmp_path / "p")  # kd26-ntsc-timeline: bin 4 = ntsc.mp4 (60 frames) on V1/A1


def listing(s: Session) -> markers.MarkerList:
    return markers.list_markers(kdenlive_doc.load(str(s.current)))


def raw_bin_markers(s: Session, bin_id: str) -> list[dict]:
    doc = kdenlive_doc.load(str(s.current))
    clip = next(
        el
        for el in doc.root
        if el.tag in ("chain", "producer")
        and props(el).get("kdenlive:id") == bin_id
        and any(e.get("producer") == el.get("id") for e in doc.main_bin.findall("entry"))
    )
    return json.loads(props(clip).get("kdenlive:markers") or "[]")


def code(s: Session, fn, *args, **kwargs) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(ProjectError) as err:
        s.apply(fn, *args, **kwargs)
    return err.value.code


def test_categories_and_empty_lists(s):
    found = listing(s)
    assert found.guides == [] and found.clip_markers == []
    assert [(c.index, c.name) for c in found.categories][:2] == [
        (0, "Category 1"),
        (1, "Category 2"),
    ]


def test_guides_round_trip_in_kdenlive_format(s):
    s.apply(markers.add_marker, 45, comment="Chapitre 2 — été", category=1)
    s.apply(markers.add_marker, 0, comment="Intro", duration=30)
    found = listing(s)
    assert [(g.position, g.duration, g.comment, g.category, g.target) for g in found.guides] == [
        (0, 30, "Intro", 0, "timeline"),
        (45, 0, "Chapitre 2 — été", 1, "timeline"),
    ]
    assert found.guides[1].timecode == "00:00:01:15"
    seq = kdenlive_doc.load(str(s.current)).sequence_tractors()[0]
    stored = json.loads(props(seq)["kdenlive:sequenceproperties.guides"])
    assert stored[1] == {"comment": "Chapitre 2 — été", "duration": 0, "pos": 45, "type": 1}
    # get_timeline shows the same guides
    assert [g.position for g in s.timeline().guides] == [0, 45]


def test_clip_markers_live_on_the_bin_clip(s):
    s.apply(markers.add_marker, 10, comment="Clap", bin_id="4")
    assert raw_bin_markers(s, "4") == [{"comment": "Clap", "duration": 0, "pos": 10, "type": 0}]
    found = listing(s)
    assert [(m.target, m.position, m.clip_name) for m in found.clip_markers] == [
        ("4", 10, "ntsc.mp4")
    ]


def test_update_and_remove(s):
    s.apply(markers.add_marker, 20, comment="old", bin_id="4")
    s.apply(markers.update_marker, 20, bin_id="4", new_position=25, comment="new", category=2)
    assert raw_bin_markers(s, "4") == [{"comment": "new", "duration": 0, "pos": 25, "type": 2}]
    s.apply(markers.remove_marker, 25, bin_id="4")
    assert raw_bin_markers(s, "4") == []
    s.apply(markers.add_marker, 5)
    s.apply(markers.remove_marker, 5)
    assert listing(s).guides == []


def test_unknown_keys_of_existing_markers_are_kept(s, tmp_path):
    edit = writer.open_for_edit(str(s.current))
    seq = edit.doc.sequence_tractors()[0]
    for p in seq.findall("property"):
        if p.get("name") == "kdenlive:sequenceproperties.guides":
            p.text = json.dumps([{"comment": "x", "pos": 7, "type": 0, "futureKey": [1, 2]}])
    writer.save(edit, str(tmp_path / "future.kdenlive"))
    s.current = tmp_path / "future.kdenlive"
    s.apply(markers.update_marker, 7, comment="y")
    seq = kdenlive_doc.load(str(s.current)).sequence_tractors()[0]
    (stored,) = json.loads(props(seq)["kdenlive:sequenceproperties.guides"])
    assert stored == {"comment": "y", "pos": 7, "type": 0, "futureKey": [1, 2]}


@pytest.mark.parametrize(
    ("args", "kwargs", "expected"),
    [
        ((-1,), {}, "invalid_argument"),
        ((60,), {"bin_id": "4"}, "invalid_range"),  # clip frames are 0..59
        ((50,), {"bin_id": "4", "duration": 20}, "invalid_range"),
        ((0,), {"category": 42}, "invalid_argument"),
        ((0,), {"bin_id": "999"}, "clip_not_found"),
        ((0,), {"bin_id": "3"}, "invalid_argument"),  # bin 3 is the sequence itself
    ],
)
def test_invalid_markers(s, args, kwargs, expected):
    assert code(s, markers.add_marker, *args, **kwargs) == expected


def test_duplicates_and_missing(s):
    s.apply(markers.add_marker, 12)
    assert code(s, markers.add_marker, 12) == "invalid_argument"
    assert code(s, markers.update_marker, 13, comment="x") == "marker_not_found"
    assert code(s, markers.remove_marker, 13) == "marker_not_found"
    s.apply(markers.add_marker, 30)
    assert code(s, markers.update_marker, 12, new_position=30) == "invalid_argument"


def test_fractional_rate_boundaries(s):
    """29.97 fps: the frame numbers are kept exactly; timecodes use drop-frame-free labels."""
    s.apply(markers.add_marker, 59, bin_id="4")
    s.apply(markers.add_marker, 10_000)
    found = listing(s)
    assert found.clip_markers[0].position == 59
    assert found.guides[0].position == 10_000


# ------------------------------------------------------------------ review cases ------
def _with_guides(s: Session, tmp_path: Path, guides: list) -> None:  # type: ignore[type-arg]
    edit = writer.open_for_edit(str(s.current))
    seq = edit.doc.sequence_tractors()[0]
    for p in seq.findall("property"):
        if p.get("name") == "kdenlive:sequenceproperties.guides":
            p.text = json.dumps(guides)
    writer.save(edit, str(tmp_path / "g.kdenlive"))
    s.current = tmp_path / "g.kdenlive"


def test_malformed_entries_are_skipped_and_kept(s, tmp_path):
    _with_guides(
        s,
        tmp_path,
        [{"pos": "abc"}, {"pos": None}, {"pos": 3, "type": "x"}, {"pos": 9, "comment": "ok"}],
    )
    assert [(g.position, g.category) for g in listing(s).guides] == [(3, 0), (9, 0)]
    s.apply(markers.update_marker, 9, comment="still ok")
    seq = kdenlive_doc.load(str(s.current)).sequence_tractors()[0]
    stored = json.loads(props(seq)["kdenlive:sequenceproperties.guides"])
    assert {"pos": "abc"} in stored and {"pos": None} in stored  # untouched


def test_unknown_existing_category_does_not_block_other_changes(s, tmp_path):
    _with_guides(s, tmp_path, [{"pos": 3, "type": 42, "comment": "x"}])
    s.apply(markers.update_marker, 3, comment="y")
    assert code(s, markers.update_marker, 3, category=43) == "invalid_argument"


def test_duplicate_positions_in_a_file_are_refused(s, tmp_path):
    _with_guides(s, tmp_path, [{"pos": 3, "comment": "a"}, {"pos": 3, "comment": "b"}])
    assert code(s, markers.update_marker, 3, comment="c") == "unsupported_structure"
    assert code(s, markers.remove_marker, 3) == "unsupported_structure"


def test_sequences_are_selected_like_get_timeline(tmp_path):
    shutil.copytree(FIX / "upstream", tmp_path / "u")
    s = Session(tmp_path / "u")
    s.current = tmp_path / "u" / "test-nesting.resaved-26.08.2.kdenlive"
    s.apply(markers.add_marker, 7, comment="active")
    active = s.timeline().sequence_uuid
    assert [g.position for g in listing(s).guides] == [7]
    assert [
        g.position for g in markers.list_markers(kdenlive_doc.load(str(s.current)), active).guides
    ] == [7]
    with pytest.raises(ProjectError) as err:
        markers.list_markers(kdenlive_doc.load(str(s.current)), "nope")
    assert err.value.code == "sequence_not_found"


def test_emoji_comments(s):
    s.apply(markers.add_marker, 1, comment="🎬 prise 2 — ok")
    assert listing(s).guides[0].comment == "🎬 prise 2 — ok"
