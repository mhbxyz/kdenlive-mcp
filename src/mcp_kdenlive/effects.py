"""A verified subset of Kdenlive effects (#14), written the way Kdenlive 26.08.2 writes them.

Each effect below was applied by Kdenlive itself (addEffect on timeline clips in the headless
GUI lab), saved, and its XML copied here: filter properties, value formats and the fixed
properties Kdenlive adds. Other installed effects are not offered: their parameters, defaults and
keyframe formats have not been verified.

Values of "animated" parameters are written as one keyframe at frame 0 (``0=<value>``), which is
what Kdenlive writes for a constant value; an existing animated value (several keyframes) is never
rewritten. The edge fades (fade from/to black) store source frames in the filter's ``in``/``out``
and follow trims and splits like the audio fades (#13).
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from mcp_kdenlive.clip_effects import FADE_PAIRS, fade_length, keyframes
from mcp_kdenlive.edits import _next_id
from mcp_kdenlive.environment import locate
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import props
from mcp_kdenlive.policy import child_env
from mcp_kdenlive.process import ProcessTimeout, run
from mcp_kdenlive.timeline_edit import (
    Item,
    Timeline,
    Track,
    _open,
    anchor_fades,
)
from mcp_kdenlive.writer import EditableProject

__all__ = ["add_effect", "catalog", "remove_effect", "update_effect"]

VERIFIED_WITH = (
    "Kdenlive 26.08.2 with MLT 7.42.0 (format and round trip); renders also checked with "
    "Kdenlive 24.12.3 and 25.04.3"
)


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # "int", "float", "bool"
    minimum: float
    maximum: float
    default: float
    unit: str
    description: str
    animated: bool = False  # written as "0=<value>"


@dataclass(frozen=True)
class Spec:
    id: str
    name: str
    service: str
    target: str  # "video" or "audio"
    params: tuple[Param, ...] = ()
    fixed: tuple[tuple[str, str], ...] = ()
    edge: str | None = None  # "start" or "end": an edge fade with in/out
    notes: str = ""


_BLACK_FADE_NOTE = (
    "Rendered by Kdenlive 25.04.3 and later; Kdenlive 24.12.3 renders the clip without the fade."
)


def _pixels(name: str) -> Param:
    return Param(name, "int", 0, 10000, 0, "pixels", f"Pixels removed from the {name} edge.")


SPECS: dict[str, Spec] = {
    spec.id: spec
    for spec in (
        Spec(
            "crop",
            "Crop",
            "crop",
            "video",
            (
                *(_pixels(n) for n in ("top", "left", "bottom", "right")),
                Param(
                    "center", "bool", 0, 1, 0, "", "Crop around the centre to the profile's ratio."
                ),
            ),
            (("center_bias", "0"), ("use_profile", "1")),
        ),
        Spec(
            "brightness",
            "Brightness",
            "brightness",
            "video",
            (
                Param(
                    "level",
                    "float",
                    0,
                    4,
                    1,
                    "factor",
                    "1 = unchanged, 2 = twice as bright.",
                    animated=True,
                ),
            ),
            (("start", "1"),),
        ),
        Spec("greyscale", "Greyscale", "greyscale", "video"),
        Spec(
            "sepia",
            "Sepia",
            "sepia",
            "video",
            (
                Param("u", "int", 0, 255, 75, "", "Chroma U (blue difference).", animated=True),
                Param("v", "int", 0, 255, 150, "", "Chroma V (red difference).", animated=True),
            ),
        ),
        Spec(
            "gamma",
            "Gamma",
            "gamma",
            "video",
            (Param("gamma", "float", 0.01, 3, 1, "factor", "1 = unchanged.", animated=True),),
        ),
        Spec(
            "fade_from_black",
            "Fade from black",
            "brightness",
            "video",
            (Param("duration", "int", 1, 100000, 30, "frames", "Length of the fade."),),
            (("start", "1"), ("level", "1"), ("alpha", "0=0;-1=1")),
            edge="start",
            notes=_BLACK_FADE_NOTE,
        ),
        Spec(
            "fade_to_black",
            "Fade to black",
            "brightness",
            "video",
            (Param("duration", "int", 1, 100000, 30, "frames", "Length of the fade."),),
            (("start", "1"), ("level", "1"), ("alpha", "0=1;-1=0")),
            edge="end",
            notes=_BLACK_FADE_NOTE,
        ),
    )
}


# ---------------------------------------------------------------- catalog ------------
class ParamInfo(BaseModel):
    name: str
    type: str
    unit: str
    minimum: float
    maximum: float
    default: float
    description: str


class EffectInfo(BaseModel):
    id: str = Field(description="Effect identifier (Kdenlive's kdenlive_id).")
    name: str
    target: str = Field(description="'video' or 'audio': the part of the clip it applies to.")
    mlt_service: str
    available: bool | None = Field(description="MLT has the service; null if not checked.")
    parameters: list[ParamInfo]
    verified_with: str
    notes: str = Field(description="Compatibility notes; empty when there are none.")


class TransitionInfo(BaseModel):
    id: str
    name: str
    tools: list[str] = Field(description="Tools that add and remove it.")
    targets: list[str] = Field(description="Track kinds it applies to.")
    mlt_services: list[str]
    min_frames: int
    default_frames: str
    constraints: list[str]
    verified_with: str


CROSSFADE = TransitionInfo(
    id="crossfade",
    name="Crossfade (Kdenlive's Mix Clips)",
    tools=["add_mix", "remove_mix"],
    targets=["video", "audio"],
    mlt_services=["luma", "mix"],
    min_frames=2,
    default_frames="1 second",
    constraints=[
        "the two clips touch on the same track",
        "their sources extend past the cut by `duration` frames in total (handles)",
        "neither clip is already in another mix at that edge",
        "grouped audio/video clips are crossfaded together",
    ],
    verified_with=VERIFIED_WITH,
)


class EffectCatalog(BaseModel):
    effects: list[EffectInfo]
    transitions: list[TransitionInfo] = Field(description="Same-track transitions.")
    notes: list[str]


@lru_cache(maxsize=4)
def _query_filters(melt: str) -> frozenset[str] | None:
    try:
        done = run([melt, "-query", "filters"], timeout=30, env=child_env())
    except (OSError, ProcessTimeout):
        return None
    names = re.findall(r"^\s*-\s*([\w.:-]+)\s*$", done.stdout, flags=re.M)
    return frozenset(names) if names else None


def available_services(env: Mapping[str, str] | None = None) -> set[str] | None:
    """MLT filter services known to the installed melt (None when it cannot be asked)."""
    melt = locate("melt", env)
    found = _query_filters(melt) if melt else None
    return set(found) if found is not None else None


def catalog(env: Mapping[str, str] | None = None) -> EffectCatalog:
    services = available_services(env)
    return EffectCatalog(
        effects=[
            EffectInfo(
                id=s.id,
                name=s.name,
                target=s.target,
                mlt_service=s.service,
                available=None if services is None else s.service in services,
                parameters=[
                    ParamInfo(
                        name=p.name,
                        type=p.kind,
                        unit=p.unit,
                        minimum=p.minimum,
                        maximum=p.maximum,
                        default=p.default,
                        description=p.description,
                    )
                    for p in s.params
                ],
                verified_with=VERIFIED_WITH,
                notes=s.notes,
            )
            for s in SPECS.values()
        ],
        transitions=[CROSSFADE],
        notes=[
            "Only these effects are offered: their formats were verified against Kdenlive. "
            "Audio level and fades have their own tools (set_clip_volume, set_clip_fades).",
        ],
    )


# ---------------------------------------------------------------- editing ------------
class EffectEditResult(BaseModel):
    operation: str
    clip: str = Field(description="Ref of the clip that holds the effects.")
    effects: list[str] = Field(description="The clip's effects after the change, in order.")
    index: int | None = Field(description="Position of the added or changed effect.")


def _spec(effect_id: str) -> Spec:
    spec = SPECS.get(effect_id)
    if spec is None:
        raise ProjectError(
            "invalid_argument",
            f"Effect {effect_id!r} is not in the verified list.",
            f"Verified effects: {sorted(SPECS)} (list_effects).",
        )
    return spec


def _value(spec: Spec, name: str, raw: Any) -> str:
    param = next((p for p in spec.params if p.name == name), None)
    if param is None:
        raise ProjectError(
            "invalid_argument",
            f"{spec.id} has no parameter {name!r}.",
            f"Parameters: {[p.name for p in spec.params] or 'none'}.",
        )
    if param.kind == "bool":
        if not isinstance(raw, bool) and raw not in (0, 1):
            raise ProjectError("invalid_argument", f"{spec.id}.{name} must be true or false.")
        text = "1" if raw else "0"
    else:
        if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
            raise ProjectError("invalid_argument", f"{spec.id}.{name} must be a number.")
        if not param.minimum <= raw <= param.maximum:
            raise ProjectError(
                "invalid_argument",
                f"{spec.id}.{name} must be between {param.minimum:g} and {param.maximum:g} "
                f"{param.unit}.".rstrip(),
            )
        if param.kind == "int":
            if raw != int(raw):
                raise ProjectError("invalid_argument", f"{spec.id}.{name} must be a whole number.")
            text = str(int(raw))
        else:
            text = f"{round(float(raw), 4):g}"
    return f"0={text}" if param.animated else text


def _target(tl: Timeline, track: Track, item: Item, spec: Spec) -> tuple[Track, Item]:
    found = [(t, i) for t, i in tl.partners(track, item) if t.kind == spec.target]
    if not found:
        raise ProjectError(
            "incompatible_track",
            f"The clip on {track.label} at {item.start} has no {spec.target} on the timeline.",
        )
    return found[0]


def _user_filters(entry: ET.Element) -> list[ET.Element]:
    return [f for f in entry.findall("filter") if not props(f).get("internal_added")]


def _result(
    tl: Timeline, track: Track, item: Item, operation: str, index: int | None
) -> EffectEditResult:
    names = [
        props(f).get("kdenlive_id") or props(f).get("mlt_service") or "?"
        for f in _user_filters(item.element)
    ]
    return EffectEditResult(
        operation=operation, clip=f"{track.label}@{item.start}", effects=names, index=index
    )


def _check_available(spec: Spec) -> None:
    services = available_services()
    if services is not None and spec.service not in services:
        raise ProjectError(
            "dependency_missing",
            f"The installed MLT has no {spec.service!r} filter (needed by {spec.id}).",
            "Install the MLT plugins (e.g. frei0r, the full mlt package) or use another effect.",
        )


def add_effect(
    edit: EditableProject,
    ref: str,
    revision: str,
    effect: str,
    params: Mapping[str, Any] | None = None,
    sequence: str | None = None,
) -> EffectEditResult:
    """Append a verified effect to the clip's video (or audio) part."""
    spec = _spec(effect)
    values = {name: _value(spec, name, raw) for name, raw in (params or {}).items()}
    _check_available(spec)
    tl = _open(edit, revision, sequence, needs_revision=True)
    track, item = _target(tl, *tl.resolve_ref(ref), spec)
    tl.check_editable(track)
    if spec.edge and any(
        props(f).get("kdenlive_id") == spec.id for f in item.element.findall("filter")
    ):
        raise ProjectError(
            "invalid_argument", f"The clip already has {spec.id}; use update_effect."
        )
    duration = _fade_duration(spec, values) if spec.edge else 0
    if spec.edge:
        _check_fade(tl, item, spec, duration)
    flt = ET.SubElement(item.element, "filter", {"id": _next_id(edit.root, "filter")})
    ordered: list[tuple[str, str]] = []
    for param in spec.params:
        if spec.edge:
            continue  # the duration lives in in/out
        default = _value(
            spec, param.name, param.default if param.kind != "bool" else bool(param.default)
        )
        ordered.append((param.name, values.get(param.name, default)))
    ordered += [*spec.fixed, ("mlt_service", spec.service), ("kdenlive_id", spec.id)]
    for name, value in ordered:
        ET.SubElement(flt, "property", {"name": name}).text = value
    if spec.edge:
        anchor_fades(tl, item.element, {spec.id: duration}, only={spec.id})
    index = _user_filters(item.element).index(flt)
    edit.changes.append(f"effects: added {spec.id} to {track.label}@{item.start}")
    return _result(tl, track, item, "add", index)


def _effect_at(item: Item, index: int) -> ET.Element:
    """The clip's own effect at `index`, as listed in get_timeline's `effects`."""
    found = _user_filters(item.element)
    if not 0 <= index < len(found):
        raise ProjectError(
            "invalid_argument",
            f"The clip has {len(found)} effect(s); index {index} is out of range.",
            "Use the position in the clip's `effects` from get_timeline.",
        )
    return found[index]


def _check_fade(tl: Timeline, item: Item, spec: Spec, duration: int) -> None:
    """A fade, with the opposite one, must fit in the clip (like set_clip_fades)."""
    source_in, source_out = tl._entry_bounds(item.element)
    other = next((b if a == spec.id else a for a, b in FADE_PAIRS.items() if spec.id in (a, b)), "")
    taken = fade_length(item.element, other, tl.doc.time) if other else 0
    if duration + taken > source_out - source_in + 1:
        raise ProjectError(
            "invalid_range",
            f"A {duration}-frame {spec.id} does not fit in the clip "
            f"({source_out - source_in + 1} frames, {taken} used by {other or 'none'}).",
            "Use a shorter fade, or shorten the opposite one.",
        )


def _fade_duration(spec: Spec, values: Mapping[str, str]) -> int:
    default = next(p.default for p in spec.params if p.name == "duration")
    return int(float(values.get("duration", default)))


def update_effect(
    edit: EditableProject,
    ref: str,
    revision: str,
    index: int,
    params: Mapping[str, Any],
    sequence: str | None = None,
) -> EffectEditResult:
    """Change parameters of the effect at `index` (as listed by get_timeline)."""
    tl = _open(edit, revision, sequence, needs_revision=True)
    track, item = tl.resolve_ref(ref)
    tl.check_editable(track)
    flt = _effect_at(item, index)
    kdenlive_id = props(flt).get("kdenlive_id", "")
    found_spec = SPECS.get(kdenlive_id)
    if found_spec is None:
        raise ProjectError(
            "unsupported_structure",
            f"Effect {index} ({kdenlive_id or props(flt).get('mlt_service')}) is not "
            "in the verified list; edit it in Kdenlive.",
        )
    spec = found_spec
    if not params:
        raise ProjectError(
            "invalid_argument",
            "No parameter to change.",
            f"Parameters of {spec.id}: {[p.name for p in spec.params] or 'none'}.",
        )
    values = {name: _value(spec, name, raw) for name, raw in params.items()}
    if spec.edge:
        duration = _fade_duration(spec, values)
        _check_fade(tl, item, spec, duration)
        anchor_fades(tl, item.element, {spec.id: duration}, only={spec.id})
    else:
        current = {p.get("name"): p for p in flt.findall("property")}
        for name, value in values.items():
            prop = current.get(name)
            if prop is not None and len(keyframes(prop.text or "")) > 1:
                raise ProjectError(
                    "unsupported_structure",
                    f"{spec.id}.{name} is animated (several keyframes); edit it in Kdenlive.",
                )
            if prop is None:
                prop = ET.SubElement(flt, "property", {"name": name})
            prop.text = value
    edit.changes.append(f"effects: updated {spec.id} on {track.label}@{item.start}")
    return _result(tl, track, item, "update", index)


def remove_effect(
    edit: EditableProject, ref: str, revision: str, index: int, sequence: str | None = None
) -> EffectEditResult:
    """Remove the effect at `index` (any effect, verified or not)."""
    tl = _open(edit, revision, sequence, needs_revision=True)
    track, item = tl.resolve_ref(ref)
    tl.check_editable(track)
    flt = _effect_at(item, index)
    name = props(flt).get("kdenlive_id") or props(flt).get("mlt_service")
    item.element.remove(flt)
    edit.changes.append(f"effects: removed {name} from {track.label}@{item.start}")
    return _result(tl, track, item, "remove", None)
