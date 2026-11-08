"""Clip audio level and fades (#13), written the way Kdenlive 26.08.2 writes them.

Kdenlive stores clip effects as ``<filter>`` children of the timeline ``<entry>``:

- **Level:** ``mlt_service=volume``, ``kdenlive_id=volume``, ``level`` as keyframes
  ``"<frame>=<dB>"``; a single ``0=<dB>`` keyframe is a constant level. ``max_gain=20dB`` caps
  the boost, so levels above +20 dB have no effect.
- **Fades:** ``mlt_service=volume`` with ``kdenlive_id=fadein`` (``gain=0``, ``end=1``) or
  ``fadeout`` (``gain=1``, ``end=0``). The filter's ``in``/``out`` are **source frames** of the
  clip instance: a fade-in of N frames spans ``[source_in, source_in + N]``, a fade-out
  ``[source_out - N, source_out]`` (verified in the GUI lab on a clip starting at source frame
  15). Kdenlive allows one of each per clip.

Only the audio part of a clip gets these effects: for an audio/video pair, the audio clip.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

from pydantic import BaseModel, Field

from mcp_kdenlive.clip_effects import effects, fade_length, keyframes, level_db
from mcp_kdenlive.edits import _next_id
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import props
from mcp_kdenlive.timeline_edit import Item, Timeline, Track, _open, anchor_fades
from mcp_kdenlive.writer import EditableProject

LEVEL_MIN, LEVEL_MAX = -100.0, 20.0
COMMON = {"window": "75", "max_gain": "20dB", "channel_mask": "-1", "mlt_service": "volume"}
FADES = {"fadein": ("0", "1"), "fadeout": ("1", "0")}


class AudioClipState(BaseModel):
    track: str
    start: int
    end: int = Field(description="Exclusive end frame.")
    level_db: float | None = Field(description="Constant level in dB; null when not set.")
    fade_in: int = Field(description="Fade-in length in frames (0 = none).")
    fade_out: int = Field(description="Fade-out length in frames (0 = none).")


class AudioEditResult(BaseModel):
    operation: str
    clips: list[AudioClipState] = Field(description="Audio clips that were changed.")


# ---------------------------------------------------------------- editing -------------
def _audio_targets(tl: Timeline, track: Track, item: Item) -> list[tuple[Track, Item]]:
    targets = [(t, i) for t, i in tl.partners(track, item) if t.kind == "audio"]
    if not targets:
        raise ProjectError(
            "incompatible_track",
            f"The clip on {track.label} at {item.start} has no audio on the timeline.",
            "Pass the audio clip, or insert the media with its audio track.",
        )
    return targets


def _state(tl: Timeline, track: Track, item: Item) -> AudioClipState:
    return AudioClipState(
        track=track.label,
        start=item.start,
        end=item.end,
        level_db=level_db(item.element),
        fade_in=fade_length(item.element, "fadein", tl.doc.time),
        fade_out=fade_length(item.element, "fadeout", tl.doc.time),
    )


def _new_filter(tl: Timeline, entry: ET.Element, values: dict[str, str]) -> ET.Element:
    flt = ET.SubElement(entry, "filter", {"id": _next_id(tl.edit.root, "filter")})
    for name, value in values.items():
        ET.SubElement(flt, "property", {"name": name}).text = value
    return flt


def _format_db(value: float) -> str:
    return f"{value:g}"


def set_clip_volume(
    edit: EditableProject,
    ref: str,
    revision: str,
    level: float,
    sequence: str | None = None,
) -> AudioEditResult:
    """Set a constant level (dB) on the clip's audio, like Kdenlive's Volume effect."""
    if not (math.isfinite(level) and LEVEL_MIN <= level <= LEVEL_MAX):
        raise ProjectError(
            "invalid_argument",
            f"level must be between {LEVEL_MIN:g} and {LEVEL_MAX:g} dB (got {level}).",
            "Kdenlive caps boosts at +20 dB; 0 dB leaves the level unchanged.",
        )
    tl = _open(edit, revision, sequence, needs_revision=True)
    track, item = tl.resolve_ref(ref)
    targets = _audio_targets(tl, track, item)
    for t, i in targets:
        tl.check_editable(t)
        existing = effects(i.element, "volume")
        if len(existing) > 1 or (
            existing and len(keyframes(props(existing[0]).get("level", "0"))) > 1
        ):
            raise ProjectError(
                "unsupported_structure",
                f"The clip on {t.label} has an animated or repeated volume effect.",
                "Edit it in Kdenlive; this tool only sets a constant level.",
            )
    changed = []
    for t, i in targets:
        existing = effects(i.element, "volume")
        if existing:
            for p in existing[0].findall("property"):
                if p.get("name") == "level":
                    p.text = f"0={_format_db(level)}"
        else:
            _new_filter(
                tl,
                i.element,
                {**COMMON, "level": f"0={_format_db(level)}", "kdenlive_id": "volume"},
            )
        changed.append(_state(tl, t, i))
    edit.changes.append(f"audio: level {_format_db(level)} dB on {ref}")
    return AudioEditResult(operation="volume", clips=changed)


def set_clip_fades(
    edit: EditableProject,
    ref: str,
    revision: str,
    fade_in: int | None = None,
    fade_out: int | None = None,
    sequence: str | None = None,
) -> AudioEditResult:
    """Set audio fade-in/fade-out lengths in frames (None: unchanged, 0: remove)."""
    if fade_in is None and fade_out is None:
        raise ProjectError("invalid_argument", "Pass fade_in and/or fade_out (frames).")
    if (fade_in is not None and fade_in < 0) or (fade_out is not None and fade_out < 0):
        raise ProjectError("invalid_argument", "Fade lengths are frame counts >= 0.")
    tl = _open(edit, revision, sequence, needs_revision=True)
    track, item = tl.resolve_ref(ref)
    targets = _audio_targets(tl, track, item)
    plan = []
    for t, i in targets:
        tl.check_editable(t)
        wanted_in = fade_length(i.element, "fadein", tl.doc.time) if fade_in is None else fade_in
        wanted_out = (
            fade_length(i.element, "fadeout", tl.doc.time) if fade_out is None else fade_out
        )
        if wanted_in + wanted_out > i.length:
            raise ProjectError(
                "invalid_range",
                f"Fades of {wanted_in} + {wanted_out} frames do not fit the {i.length}-frame "
                f"clip on {t.label}.",
            )
        plan.append((t, i, wanted_in, wanted_out))
    changed = []
    for t, i, wanted_in, wanted_out in plan:
        for kdenlive_id, length in (("fadein", wanted_in), ("fadeout", wanted_out)):
            for old in effects(i.element, kdenlive_id):
                i.element.remove(old)
            if length:
                gain, end = FADES[kdenlive_id]
                _new_filter(
                    tl, i.element, {**COMMON, "kdenlive_id": kdenlive_id, "gain": gain, "end": end}
                )
        anchor_fades(tl, i.element, {"fadein": wanted_in, "fadeout": wanted_out})
        changed.append(_state(tl, t, i))
    edit.changes.append(f"audio: fades in {fade_in} / out {fade_out} frames on {ref}")
    return AudioEditResult(operation="fades", clips=changed)
