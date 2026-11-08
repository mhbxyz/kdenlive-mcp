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
    assert catalog["crop"].verified_with == "Kdenlive 26.08.2"


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
    assert (fade_in["mlt_service"], fade_in["alpha"], fade_in["level"]) == (
        "brightness",
        "0=0;-1=1",
        "1",
    )
    assert fade_out["alpha"] == "0=1;-1=0"
    s.apply(te.trim_clip, "V1@0", s.rev(), start=5)  # edge fades do not block start trims
    fade_in, _ = filters(s, "V1")
    assert fade_in["in"] == "00:00:00.167"


# ------------------------------------------------------------------ crossfades --------
def test_add_and_remove_a_crossfade(s):
    s.apply(te.trim_clip, "V1@0", s.rev(), end=40)  # [0,40) src 0..39
    s.apply(te.insert_clip, "4", "V1", 40, 10, 49)  # [40,80) src 10..49
    r = s.apply(effects.add_mix, "V1@0", s.rev(), 20)
    expected = [("V1@0", 0, 50), ("V1@30", 30, 80), ("A1@0", 0, 50), ("A1@30", 30, 80)]
    assert [(c.ref, c.start, c.end) for c in r.clips] == expected
    for label, service in (("V1", "luma"), ("A1", "mix")):
        track = next(t for t in s.timeline().tracks if t.label == label)
        assert [(m.service, m.start, m.end, m.cut) for m in track.mixes] == [(service, 30, 50, 40)]
        assert [c.layer for c in track.clips] == [0, 1]
    with pytest.raises(ProjectError):
        s.apply(effects.add_mix, "V1@0", s.rev(), 20)  # already mixed
    s.apply(effects.remove_mix, "V1@0", s.rev())
    v1 = next(t for t in s.timeline().tracks if t.label == "V1")
    assert v1.mixes == [] and [(c.start, c.end) for c in v1.clips] == [(0, 40), (40, 80)]


@pytest.mark.parametrize(
    ("duration", "expected"),
    [(0, "invalid_argument"), (500, "invalid_range")],
)
def test_crossfade_limits(s, duration, expected):
    s.apply(te.trim_clip, "V1@0", s.rev(), end=40)
    s.apply(te.insert_clip, "4", "V1", 40, 10, 49)
    assert code(s, effects.add_mix, "V1@0", s.rev(), duration) == expected


def test_crossfade_needs_an_adjacent_clip(s):
    assert code(s, effects.add_mix, "V1@0", s.rev(), 10) == "invalid_argument"
