"""A verified subset of effects and same-track transitions (#14), in Kdenlive 26.08.2's format.

Formats captured from Kdenlive itself (addEffect on timeline clips, then Save As, GUI lab):

    crop        top/left/bottom/right (pixels), center, center_bias=0, use_profile=1
    brightness  level="0=<factor>" (1 = unchanged), start=1
    greyscale   (no parameter)
    sepia       u="0=<0-255>", v="0=<0-255>"
    gamma       gamma="0=<factor>"
    fade_from_black / fade_to_black   brightness filter with in/out at the clip's edges

and the crossfade (same-track mix) from tests/fixtures/kd26/kd26-mixes.kdenlive (#61).
"""

from __future__ import annotations

import itertools
import shutil
from pathlib import Path

import pytest
from test_timeline_edit import Session

from mcp_kdenlive import effects, writer
from mcp_kdenlive import timeline_edit as te
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import props

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def s(tmp_path) -> Session:
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    return Session(tmp_path / "p")  # kd26-ntsc-timeline: AV clip [0,60) on V1 + A1


def clip(s: Session, label: str, index: int = 0):  # type: ignore[no-untyped-def]
    return next(t for t in s.timeline().tracks if t.label == label).clips[index]


def filters(s: Session, label: str, index: int = 0) -> list[dict[str, str | None]]:
    edit = writer.open_for_edit(str(s.current))
    tl = te.Timeline(edit, edit.doc.sequence_tractors()[0])
    _, item = tl.resolve_ref(clip(s, label, index).ref)
    return [
        {"in": f.get("in"), "out": f.get("out"), **props(f)} for f in item.element.findall("filter")
    ]


def code(s: Session, fn, *args, **kwargs) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(ProjectError) as err:
        s.apply(fn, *args, **kwargs)
    return err.value.code


# ------------------------------------------------------------------ catalog -----------
def test_catalog_lists_verified_effects_with_parameters():
    catalog = {e.id: e for e in effects.catalog().effects}
    assert {"crop", "brightness", "greyscale", "sepia", "gamma", "fade_from_black",
            "fade_to_black"} <= set(catalog)  # fmt: skip
    crop = {p.name: p for p in catalog["crop"].parameters}
    assert (crop["top"].unit, crop["top"].minimum, crop["top"].default) == ("pixels", 0, 0)
    assert catalog["brightness"].target == "video"
    assert catalog["crop"].verified_with.startswith("Kdenlive 26.08.2 with MLT")


# ------------------------------------------------------------------ add / update -------
def test_add_effects_in_kdenlive_format(s):
    s.apply(effects.add_effect, "V1@0", s.rev(), "crop", {"top": 20, "bottom": 20})
    s.apply(effects.add_effect, "V1@0", s.rev(), "brightness", {"level": 1.5})
    s.apply(effects.add_effect, "A1@0", s.rev(), "greyscale")  # video effect on the AV pair
    crop, bright, grey = filters(s, "V1")
    assert {k: crop[k] for k in ("mlt_service", "kdenlive_id", "top", "left", "bottom", "right",
            "center", "center_bias", "use_profile")} == {"mlt_service": "crop",
            "kdenlive_id": "crop", "top": "20", "left": "0", "bottom": "20", "right": "0",
            "center": "0", "center_bias": "0", "use_profile": "1"}  # fmt: skip
    assert (bright["level"], bright["start"], bright["mlt_service"]) == ("0=1.5", "1", "brightness")
    assert grey["kdenlive_id"] == "greyscale"
    assert filters(s, "A1") == []
    assert clip(s, "V1").effects == ["crop", "brightness", "greyscale"]


def test_update_and_remove_by_index(s):
    s.apply(effects.add_effect, "V1@0", s.rev(), "sepia")
    s.apply(effects.add_effect, "V1@0", s.rev(), "gamma", {"gamma": 1.2})
    s.apply(effects.update_effect, "V1@0", s.rev(), 0, {"u": 90})
    sepia, gamma = filters(s, "V1")
    assert (sepia["u"], sepia["v"], gamma["gamma"]) == ("0=90", "0=150", "0=1.2")
    s.apply(effects.remove_effect, "V1@0", s.rev(), 0)
    assert [f["kdenlive_id"] for f in filters(s, "V1")] == ["gamma"]


@pytest.mark.parametrize(
    ("effect", "params", "expected"),
    [
        ("blur", {}, "invalid_argument"),
        ("crop", {"top": -1}, "invalid_argument"),
        ("crop", {"zoom": 2}, "invalid_argument"),
        ("brightness", {"level": 9}, "invalid_argument"),
        ("brightness", {"level": "high"}, "invalid_argument"),
    ],
)
def test_invalid_effects(s, effect, params, expected):
    assert code(s, effects.add_effect, "V1@0", s.rev(), effect, params) == expected


def test_audio_only_clip_refuses_video_effects(s):
    s.apply(te.insert_clip, "6", "A2", 0, 0, 29)
    assert code(s, effects.add_effect, "A2@0", s.rev(), "crop") == "incompatible_track"


def test_existing_animated_effects_are_not_rewritten(s, tmp_path):
    s.apply(effects.add_effect, "V1@0", s.rev(), "brightness", {"level": 1.2})
    text = s.current.read_text(encoding="utf-8").replace(">0=1.2<", ">0=1;30=2<")
    animated = tmp_path / "animated.kdenlive"
    animated.write_text(text, encoding="utf-8")
    s.current = animated
    assert (
        code(s, effects.update_effect, "V1@0", s.rev(), 0, {"level": 1}) == "unsupported_structure"
    )
    s.apply(effects.add_effect, "V1@0", s.rev(), "greyscale")  # other effects keep theirs
    assert filters(s, "V1")[0]["level"] == "0=1;30=2"


def test_unavailable_plugin(s, monkeypatch):
    monkeypatch.setattr(effects, "available_services", lambda env=None: {"crop"})
    assert code(s, effects.add_effect, "V1@0", s.rev(), "sepia") == "dependency_missing"


# ------------------------------------------------------------------ video fades -------
def test_video_fades_follow_the_clip(s):
    s.apply(effects.add_effect, "V1@0", s.rev(), "fade_from_black", {"duration": 15})
    s.apply(effects.add_effect, "V1@0", s.rev(), "fade_to_black", {"duration": 10})
    fade_in, fade_out = filters(s, "V1")
    # Kdenlive's "Fade from/to Black" checked: the brightness fades, so the picture really goes
    # black whatever is below (the unchecked default fades the alpha, which Kdenlive 24.12.3 and
    # MLT 7.22 render as no fade at all on a single track)
    assert (fade_in["mlt_service"], fade_in["level"], fade_in["alpha"]) == (
        "brightness",
        "0=0;-1=1",
        "1",
    )
    assert (fade_out["level"], fade_out["alpha"]) == ("0=1;-1=0", "1")
    s.apply(te.trim_clip, "V1@0", s.rev(), start=5)  # edge fades do not block start trims
    fade_in, _ = filters(s, "V1")
    assert fade_in["in"] == "00:00:00.167"


# ------------------------------------------------------------------ crossfades --------
def test_add_and_remove_a_crossfade(s):
    s.apply(te.trim_clip, "V1@0", s.rev(), end=40)  # [0,40) src 0..39
    s.apply(te.insert_clip, "4", "V1", 40, 10, 49)  # [40,80) src 10..49
    r = s.apply(te.add_mix, "V1@0", s.rev(), 20)
    expected = [("V1@0", 0, 50), ("V1@30", 30, 80), ("A1@0", 0, 50), ("A1@30", 30, 80)]
    assert [(c.ref, c.start, c.end) for c in r.clips] == expected
    for label, service in (("V1", "luma"), ("A1", "mix")):
        track = next(t for t in s.timeline().tracks if t.label == label)
        assert [(m.service, m.start, m.end, m.cut) for m in track.mixes] == [(service, 30, 50, 40)]
        assert [c.layer for c in track.clips] == [0, 1]
    with pytest.raises(ProjectError) as again:
        s.apply(te.add_mix, "V1@0", s.rev(), 20)  # already mixed: say so, not "move"
    assert again.value.code == "invalid_argument"
    assert "already" in str(again.value) and "remove_mix" in str(again.value)
    s.apply(te.remove_mix, "V1@0", s.rev())
    v1 = next(t for t in s.timeline().tracks if t.label == "V1")
    assert v1.mixes == [] and [(c.start, c.end) for c in v1.clips] == [(0, 40), (40, 80)]


@pytest.mark.parametrize(
    ("duration", "expected"),
    [(0, "invalid_argument"), (500, "invalid_range")],
)
def test_crossfade_limits(s, duration, expected):
    s.apply(te.trim_clip, "V1@0", s.rev(), end=40)
    s.apply(te.insert_clip, "4", "V1", 40, 10, 49)
    assert code(s, te.add_mix, "V1@0", s.rev(), duration) == expected


def test_crossfade_needs_an_adjacent_clip(s):
    assert code(s, te.add_mix, "V1@0", s.rev(), 10) == "invalid_argument"


# ------------------------------------------------------------------ review fixes ------
def two_clips(s: Session) -> None:
    s.apply(te.trim_clip, "V1@0", s.rev(), end=40)  # [0,40) src 0..39
    s.apply(te.insert_clip, "4", "V1", 40, 10, 49)  # [40,80) src 10..49


def edges(s: Session, label: str, index: int = 0) -> dict[str, tuple[int, int]]:
    """Fade filters of a clip as (in, out) source frames, by kdenlive_id."""
    edit = writer.open_for_edit(str(s.current))
    frames = edit.doc.time
    return {
        str(f["kdenlive_id"]): (frames(f["in"] or "0"), frames(f["out"] or "0"))
        for f in filters(s, label, index)
        if str(f["kdenlive_id"]).startswith(("fade", "fadein", "fadeout"))
    }


def test_crossfades_keep_edge_fades_at_the_clip_edges(s):
    two_clips(s)
    s.apply(effects.add_effect, "V1@0", s.rev(), "fade_to_black", {"duration": 10})
    s.apply(effects.add_effect, "V1@40", s.rev(), "fade_from_black", {"duration": 10})
    assert edges(s, "V1", 0) == {"fade_to_black": (29, 39)}
    assert edges(s, "V1", 1) == {"fade_from_black": (10, 20)}
    s.apply(te.add_mix, "V1@0", s.rev(), 20)  # left src 0..49, right src 0..49 from 30
    assert edges(s, "V1", 0) == {"fade_to_black": (39, 49)}
    assert edges(s, "V1", 1) == {"fade_from_black": (0, 10)}
    s.apply(te.remove_mix, "V1@0", s.rev())
    assert edges(s, "V1", 0) == {"fade_to_black": (29, 39)}
    assert edges(s, "V1", 1) == {"fade_from_black": (10, 20)}


def test_crossfade_refuses_to_shift_animated_keyframes(s, tmp_path):
    two_clips(s)
    s.apply(effects.add_effect, "V1@40", s.rev(), "brightness", {"level": 1.2})
    text = s.current.read_text(encoding="utf-8").replace(">0=1.2<", ">0=1;30=2<")
    s.current = tmp_path / "animated.kdenlive"
    s.current.write_text(text, encoding="utf-8")
    assert code(s, te.add_mix, "V1@0", s.rev(), 20) == "unsupported_structure"


def test_chained_crossfades_are_refused_without_changes(s):
    two_clips(s)
    s.apply(te.insert_clip, "4", "V1", 80, 10, 49)  # [80,120)
    s.apply(te.add_mix, "V1@40", s.rev(), 10)  # the middle clip ends in a mix
    before = s.current.read_bytes()
    assert code(s, te.add_mix, "V1@0", s.rev(), 10) == "unsupported_structure"
    assert s.current.read_bytes() == before


def test_remove_mix_without_a_mix(s):
    two_clips(s)
    with pytest.raises(ProjectError) as err:
        s.apply(te.remove_mix, "V1@0", s.rev())
    assert err.value.code == "invalid_argument"


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (("fade_to_black", 500), None, "invalid_range"),
        (("fade_from_black", 40), ("fade_to_black", 30), "invalid_range"),
    ],
)
def test_fades_must_fit_in_the_clip(s, first, second, expected):  # V1@0 lasts 60 frames
    if second is None:
        assert (
            code(s, effects.add_effect, "V1@0", s.rev(), first[0], {"duration": first[1]})
            == expected
        )
        return
    s.apply(effects.add_effect, "V1@0", s.rev(), first[0], {"duration": first[1]})
    assert (
        code(s, effects.add_effect, "V1@0", s.rev(), second[0], {"duration": second[1]}) == expected
    )


def test_update_a_fade(s):
    s.apply(effects.add_effect, "V1@0", s.rev(), "fade_to_black", {"duration": 10})
    assert code(s, effects.update_effect, "V1@0", s.rev(), 0, {}) == "invalid_argument"
    assert code(s, effects.update_effect, "V1@0", s.rev(), 0, {"duration": 61}) == "invalid_range"
    s.apply(effects.update_effect, "V1@0", s.rev(), 0, {"duration": 20})
    assert edges(s, "V1") == {"fade_to_black": (39, 59)}


def test_catalog_describes_the_crossfade_and_versions():
    catalog = effects.catalog()
    (mix,) = catalog.transitions
    assert (mix.id, mix.tools, mix.min_frames) == ("crossfade", ["add_mix", "remove_mix"], 2)
    assert mix.targets == ["video", "audio"]
    assert "MLT 7.42.0" in catalog.effects[0].verified_with


def test_crossfades_do_not_move_the_clips_after_them_and_can_chain(s):
    """Found by the vertical-short demo (#92): extending the left clip pushed later clips."""
    s.apply(te.trim_clip, "V1@0", s.rev(), end=40)  # A [0,40) src 0..39
    s.apply(te.insert_clip, "4", "V1", 40, 10, 49)  # B [40,80) src 10..49
    s.apply(te.insert_clip, "4", "V1", 80, 10, 49)  # C [80,120) src 10..49
    s.apply(te.add_mix, "V1@0", s.rev(), 10)
    v1 = next(t for t in s.timeline().tracks if t.label == "V1")
    assert [(c.start, c.end, c.layer) for c in v1.clips] == [(0, 45, 0), (35, 80, 1), (80, 120, 0)]
    s.apply(te.add_mix, "V1@35", s.rev(), 10)  # B (layer 1) into C (back to layer 0)
    v1 = next(t for t in s.timeline().tracks if t.label == "V1")
    assert [(c.start, c.end, c.layer) for c in v1.clips] == [(0, 45, 0), (35, 85, 1), (75, 120, 0)]
    assert [(m.start, m.end, m.cut) for m in v1.mixes] == [(35, 45, 40), (75, 85, 80)]
    s.apply(te.remove_mix, "V1@35", s.rev())
    s.apply(te.remove_mix, "V1@0", s.rev())
    v1 = next(t for t in s.timeline().tracks if t.label == "V1")
    assert [(c.start, c.end) for c in v1.clips] == [(0, 40), (40, 80), (80, 120)]
    assert v1.mixes == []


def test_crossfade_into_a_clip_already_on_the_other_layer(s, tmp_path):
    """Found by the rescue demo (#92): in Kdenlive's own layout the middle clip sits on layer 1
    and ends in the next crossfade; a new crossfade before it must not need to move it."""
    s.current = s.ws / "kd26-mixes.kdenlive"
    s.apply(te.remove_mix, "V1@0", s.rev())
    s.apply(te.add_mix, "V1@0", s.rev(), 12)
    v1 = next(t for t in s.timeline().tracks if t.label == "V1")
    assert [(m.start, m.end) for m in v1.mixes] == [(34, 46), (70, 90)]  # cut back at 40
    assert [(c.start, c.end, c.layer) for c in v1.clips] == [(0, 46, 0), (34, 90, 1), (70, 130, 0)]


def consistent(s: Session, label: str = "V1") -> list[tuple[int, int, int]]:
    """Clips of a track as (start, end, layer), after checking the track is coherent."""
    track = next(t for t in s.timeline().tracks if t.label == label)
    spans = [(c.start, c.end, c.layer) for c in track.clips]
    for layer in (0, 1):
        on = sorted((a, b) for a, b, la in spans if la == layer)
        assert all(b <= c for (_, b), (c, _) in itertools.pairwise(on)), spans
    ends = {(a, la) for a, _, la in spans} | {(b, la) for _, b, la in spans}
    for m in track.mixes:
        assert any(e == m.end for e, _ in ends) and any(e == m.start for e, _ in ends), track.mixes
    mixes = sorted((m.start, m.end) for m in track.mixes)
    assert all(b <= c for (_, b), (c, _) in itertools.pairwise(mixes)), mixes
    return spans


@pytest.mark.parametrize("duration", [20, 26, 30, 40, 60, 70])
def test_long_crossfade_cannot_run_into_the_next_one(s, duration):
    """Review of #92: the left clip must not grow into the next crossfade."""
    s.current = s.ws / "kd26-mixes.kdenlive"
    s.apply(te.remove_mix, "V1@0", s.rev())
    try:
        s.apply(te.add_mix, "V1@0", s.rev(), duration)
    except ProjectError as err:
        assert err.code in ("invalid_range", "overlap")
        return
    spans = consistent(s)
    assert (70, 130, 0) in spans  # the last clip did not move


@pytest.mark.parametrize("duration", [22, 28, 30, 32, 34])
def test_crossfade_moving_across_never_overlaps_an_earlier_one(s, duration):
    s.apply(te.trim_clip, "V1@0", s.rev(), end=40)
    s.apply(te.insert_clip, "4", "V1", 40, 10, 49)
    s.apply(te.insert_clip, "4", "V1", 80, 20, 39)  # C, short
    s.apply(te.insert_clip, "4", "V1", 100, 25, 59)  # D
    s.apply(te.add_mix, "V1@0", s.rev(), 10)
    s.apply(te.add_mix, "V1@35", s.rev(), 10)
    try:
        s.apply(te.add_mix, "V1@75", s.rev(), duration)
    except ProjectError as err:
        assert err.code in ("invalid_range", "overlap")
        return
    spans = consistent(s)
    assert max(b for _, b, _ in spans) == 135  # D keeps its end


@pytest.mark.parametrize("duration", [20, 26, 30])
def test_left_clip_cannot_grow_into_the_next_crossfade(s, duration):
    """Review of #92: B sits on layer 1 and ends in a crossfade into C; A -> B must not extend A
    over C's start."""
    s.apply(te.trim_clip, "V1@0", s.rev(), end=40)
    s.apply(te.insert_clip, "4", "V1", 40, 10, 29)  # B, short
    s.apply(te.insert_clip, "4", "V1", 60, 10, 49)  # C
    s.apply(te.add_mix, "V1@0", s.rev(), 4)
    s.apply(te.add_mix, "V1@38", s.rev(), 10)
    s.apply(te.remove_mix, "V1@0", s.rev())
    before = consistent(s)
    c = max(before, key=lambda span: span[0])
    try:
        s.apply(te.add_mix, "V1@0", s.rev(), duration)
    except ProjectError as err:
        assert err.code in ("invalid_range", "overlap")
        return
    assert c in consistent(s)  # C did not move
