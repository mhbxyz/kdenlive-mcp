"""Timeline guides and clip markers (#12), stored like Kdenlive 26.08.2 stores them.

- **Guides** (timeline markers): JSON list in the sequence tractor's
  ``kdenlive:sequenceproperties.guides``; ``pos`` and ``duration`` in timeline frames.
- **Clip markers**: the same JSON list in the **bin clip**'s ``kdenlive:markers``; ``pos`` and
  ``duration`` in source frames of that clip. Every instance of the clip on the timeline shows
  them (Kdenlive keeps them on the bin clip only).
- Each marker is ``{"comment": str, "duration": int, "pos": int, "type": category}``; categories
  come from ``kdenlive:docproperties.guidesCategories`` (index, comment, color).

A marker is identified by its target (``"timeline"`` or a bin clip id) and its position: Kdenlive
keeps at most one marker per position. Keys this server does not know are preserved.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from pydantic import BaseModel, Field

from mcp_kdenlive import project
from mcp_kdenlive.edits import _sequence, _set_prop
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import KdenliveDocument, props
from mcp_kdenlive.timecode import frames_to_timecode
from mcp_kdenlive.writer import EditableProject

GUIDES = "kdenlive:sequenceproperties.guides"
CLIP_MARKERS = "kdenlive:markers"
TIMELINE = "timeline"


class Category(BaseModel):
    index: int
    name: str | None
    color: str | None


class Marker(BaseModel):
    target: str = Field(description="'timeline' for a guide, otherwise the bin clip id.")
    clip_name: str | None = Field(default=None, description="Bin clip name (clip markers).")
    position: int = Field(description="Frame: timeline frame (guide) or source frame (clip).")
    timecode: str
    duration: int = Field(description="Frames; 0 for a point marker.")
    comment: str
    category: int


class MarkerList(BaseModel):
    categories: list[Category]
    guides: list[Marker]
    clip_markers: list[Marker]


class MarkerEditResult(BaseModel):
    operation: str
    marker: Marker | None = Field(description="The marker after the change; null if removed.")
    count: int = Field(description="Markers now on the target.")


# ---------------------------------------------------------------- storage -------------
def _load(raw: str | None) -> list[dict[str, Any]]:
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProjectError("unsupported_structure", "Markers data is not valid JSON.") from exc
    if not isinstance(data, list) or not all(isinstance(m, dict) for m in data):
        raise ProjectError("unsupported_structure", "Markers data is not a list of markers.")
    return data


def _int(value: Any) -> int | None:
    """An int from a marker field, or None for anything else (kept untouched, not listed)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _valid(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in items if _int(m.get("pos")) is not None]


def _dump(items: list[dict[str, Any]]) -> str:
    # Kdenlive (Qt) writes sorted keys, 4-space indents, UTF-8, and "[\n]\n" when empty.
    if not items:
        return "[\n]\n"
    ordered = sorted(items, key=lambda m: _int(m.get("pos")) or 0)
    return json.dumps(ordered, indent=4, sort_keys=True, ensure_ascii=False) + "\n"


def _bin_clips(doc: KdenliveDocument) -> dict[str, ET.Element]:
    in_bin = {e.get("producer") for e in doc.main_bin.findall("entry")}
    return {
        props(el)["kdenlive:id"]: el
        for el in doc.root
        if el.tag in ("chain", "producer", "tractor")
        and el.get("id") in in_bin
        and props(el).get("kdenlive:id")
    }


def _categories(doc: KdenliveDocument) -> list[Category]:
    raw = doc.doc_property("guidesCategories")
    try:
        data = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        data = []
    return [
        Category(index=c["index"], name=c.get("comment"), color=c.get("color"))
        for c in data
        if isinstance(c, dict) and isinstance(c.get("index"), int)
    ]


def _marker(doc: KdenliveDocument, target: str, item: dict[str, Any], name: str | None) -> Marker:
    pos = _int(item.get("pos")) or 0
    return Marker(
        target=target,
        clip_name=name,
        position=pos,
        timecode=frames_to_timecode(pos, doc.rate),
        duration=_int(item.get("duration")) or 0,
        comment=str(item.get("comment", "")),
        category=_int(item.get("type")) or 0,
    )


def _clip_name(clip: ET.Element) -> str | None:
    p = props(clip)
    return p.get("kdenlive:clipname") or (p.get("resource") or "").rsplit("/", 1)[-1] or None


def list_markers(doc: KdenliveDocument, sequence: str | None = None) -> MarkerList:
    """Guides of a sequence (default: the active one, like get_timeline) and clip markers."""
    view = project.select_sequence(doc, sequence)
    raw = props(view.tractor).get(GUIDES)
    if raw is None:  # documents before Kdenlive 23.04 kept guides in the document properties
        raw = doc.doc_property("guides")
    guides = [_marker(doc, TIMELINE, g, None) for g in _valid(_load(raw))]
    clip_markers = [
        _marker(doc, bin_id, m, _clip_name(clip))
        for bin_id, clip in sorted(_bin_clips(doc).items())
        if clip.tag != "tractor"
        for m in _valid(_load(props(clip).get(CLIP_MARKERS)))
    ]
    return MarkerList(
        categories=_categories(doc),
        guides=sorted(guides, key=lambda g: g.position),
        clip_markers=sorted(clip_markers, key=lambda m: (m.target, m.position)),
    )


# ---------------------------------------------------------------- editing -------------
class _Target:
    """Where a marker list is stored, with its frame limits."""

    def __init__(self, edit: EditableProject, bin_id: str | None, sequence: str | None) -> None:
        doc = edit.doc
        if bin_id is None:
            self.element = _sequence(edit, sequence)
            self.prop, self.name, self.label, self.length = GUIDES, None, TIMELINE, None
            return
        clip = _bin_clips(doc).get(str(bin_id))
        if clip is None:
            raise ProjectError("clip_not_found", f"No bin clip with id {bin_id!r}.")
        if clip.tag == "tractor" or props(clip).get("mlt_service") == "tractor":
            raise ProjectError("invalid_argument", f"Bin clip {bin_id} is a sequence.")
        length = props(clip).get("length")
        self.element, self.prop, self.label = clip, CLIP_MARKERS, str(bin_id)
        self.name = _clip_name(clip)
        self.length = doc.time(length, what="clip length") if length else None

    def items(self) -> list[dict[str, Any]]:
        return _load(props(self.element).get(self.prop))

    def save(self, items: list[dict[str, Any]]) -> None:
        _set_prop(self.element, self.prop, _dump(items))


def _check(
    edit: EditableProject, target: _Target, position: int, duration: int, category: int | None
) -> None:
    if position < 0 or duration < 0:
        raise ProjectError("invalid_argument", "Positions and durations are frames >= 0.")
    if target.length is not None and position + max(duration, 1) > target.length:
        raise ProjectError(
            "invalid_range",
            f"Clip {target.label} has frames 0..{target.length - 1}; a marker at {position} "
            f"lasting {duration} frames does not fit.",
        )
    known = {c.index for c in _categories(edit.doc)}
    if category is not None and known and category not in known:
        raise ProjectError(
            "invalid_argument", f"Unknown category {category}; categories: {sorted(known)}."
        )


def _find(items: list[dict[str, Any]], position: int, target: _Target) -> dict[str, Any]:
    found = [m for m in items if _int(m.get("pos")) == position]
    where = "the timeline" if target.label == TIMELINE else f"clip {target.label}"
    if not found:
        raise ProjectError("not_found", f"No marker at frame {position} on {where}.")
    if len(found) > 1:
        raise ProjectError(
            "unsupported_structure",
            f"{len(found)} markers share frame {position} on {where}; Kdenlive keeps one.",
            "Fix them in Kdenlive first.",
        )
    return found[0]


def _result(
    edit: EditableProject, target: _Target, operation: str, item: dict[str, Any] | None
) -> MarkerEditResult:
    marker = _marker(edit.doc, target.label, item, target.name) if item is not None else None
    return MarkerEditResult(operation=operation, marker=marker, count=len(target.items()))


def add_marker(
    edit: EditableProject,
    position: int,
    *,
    comment: str = "",
    category: int = 0,
    duration: int = 0,
    bin_id: str | None = None,
    sequence: str | None = None,
) -> MarkerEditResult:
    target = _Target(edit, bin_id, sequence)
    _check(edit, target, position, duration, category)
    items = target.items()
    if any(_int(m.get("pos")) == position for m in items):
        raise ProjectError(
            "invalid_argument",
            f"There is already a marker at frame {position}; Kdenlive keeps one per position.",
            "Use update_marker to change it.",
        )
    item = {"comment": comment, "duration": duration, "pos": position, "type": category}
    target.save([*items, item])
    edit.changes.append(f"markers: added at {position} on {target.label}")
    return _result(edit, target, "add", item)


def update_marker(
    edit: EditableProject,
    position: int,
    *,
    bin_id: str | None = None,
    new_position: int | None = None,
    comment: str | None = None,
    category: int | None = None,
    duration: int | None = None,
    sequence: str | None = None,
) -> MarkerEditResult:
    target = _Target(edit, bin_id, sequence)
    items = target.items()
    item = _find(items, position, target)
    pos = position if new_position is None else new_position
    if pos != position and any(_int(m.get("pos")) == pos for m in items):
        raise ProjectError("invalid_argument", f"There is already a marker at frame {pos}.")
    new_duration = (_int(item.get("duration")) or 0) if duration is None else duration
    _check(edit, target, pos, new_duration, category)
    item["pos"] = pos
    if comment is not None:
        item["comment"] = comment
    if duration is not None:
        item["duration"] = duration
    if category is not None:
        item["type"] = category
    target.save(items)
    edit.changes.append(f"markers: updated {position} on {target.label}")
    return _result(edit, target, "update", item)


def remove_marker(
    edit: EditableProject,
    position: int,
    *,
    bin_id: str | None = None,
    sequence: str | None = None,
) -> MarkerEditResult:
    target = _Target(edit, bin_id, sequence)
    items = target.items()
    item = _find(items, position, target)
    target.save([m for m in items if m is not item])
    edit.changes.append(f"markers: removed {position} on {target.label}")
    return _result(edit, target, "remove", None)
