"""Frame-accurate timeline operations: insert, move, trim, split, remove (#11).

Conventions (see docs/tools.md):

* positions are integer frames at the project rate; timeline ranges are ``[start, end)``,
  source ranges are inclusive ``source_in..source_out`` like MLT;
* nothing ripples: neighbouring clips never move, and an operation that would overlap another
  clip fails with ``[overlap]`` (there is no overwrite mode);
* clips are addressed by the ``ref`` returned by get_timeline (``track:layer:entry``) together
  with the file ``revision`` (its sha256), so stale references are rejected;
* a clip grouped with its audio/video counterpart (Kdenlive ``AVSplit`` group) is moved, trimmed,
  split and removed together with it, like Kdenlive does by default;
* like Kdenlive 26.08, every timeline clip instance gets its own ``<chain>`` copied from the
  bin clip, with ``set.test_audio``/``set.test_image`` disabling the unused half of AV media.

Refused (``[unsupported_structure]``): tracks using same-track mixes, groups other than AV pairs,
and splitting or trimming the start of clips whose effects are animated (keyframes would shift).
"""

from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from mcp_kdenlive import project
from mcp_kdenlive.edits import _next_id, _sequence, _set_prop
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import props
from mcp_kdenlive.timecode import frames_to_clock
from mcp_kdenlive.writer import EditableProject

KEYFRAMES = re.compile(r"(^|;)\s*-?[\d:.]+\s*[~|!]?=")


class ClipPlacement(BaseModel):
    track: str
    start: int
    end: int = Field(description="Exclusive end frame.")
    source_in: int
    source_out: int


class TimelineEditResult(BaseModel):
    operation: str
    clips: list[ClipPlacement] = Field(description="Resulting placement of the affected clips.")
    sequence_duration_frames: int


# ---------------------------------------------------------------- model --------------
@dataclass
class Item:
    element: ET.Element
    start: int
    length: int

    @property
    def is_blank(self) -> bool:
        return self.element.tag == "blank"

    @property
    def end(self) -> int:
        return self.start + self.length


@dataclass
class Track:
    index: int  # MLT index in the sequence
    label: str
    kind: str
    tractor: ET.Element
    playlist: ET.Element  # layer 0
    other: ET.Element | None  # layer 1


class Timeline:
    def __init__(self, edit: EditableProject, sequence: ET.Element) -> None:
        self.edit = edit
        self.doc = edit.doc
        self.sequence = sequence
        view = project.timeline(
            self.doc,
            props(sequence).get("kdenlive:uuid") or sequence.get("id"),
            max_clips=project.MAX_CLIPS_LIMIT,
        )
        self.tracks: dict[str, Track] = {}
        by_index = {t.index: t for t in view.tracks}
        refs = sequence.findall("track")
        for index, info in by_index.items():
            tractor = self.doc.by_id[refs[index].get("producer") or ""]
            layers = [self.doc.by_id.get(t.get("producer") or "") for t in tractor.findall("track")]
            if not layers or layers[0] is None:
                continue
            self.tracks[info.label] = Track(
                index,
                info.label,
                info.kind,
                tractor,
                layers[0],
                layers[1] if len(layers) > 1 else None,
            )

    # -- playlist helpers ------------------------------------------------------------
    def items(self, track: Track) -> list[Item]:
        out, pos = [], 0
        for el in track.playlist:
            if el.tag == "blank":
                length = self.doc.time(el.get("length"), what="blank length")
            elif el.tag == "entry":
                length = self._entry_bounds(el)[1] - self._entry_bounds(el)[0] + 1
            else:
                continue
            out.append(Item(el, pos, length))
            pos += length
        return out

    def _entry_bounds(self, entry: ET.Element) -> tuple[int, int]:
        producer = self.doc.by_id.get(entry.get("producer") or "")
        default_out = None
        if producer is not None:
            length = props(producer).get("length")
            default_out = self.doc.time(length, what="length") - 1 if length else None
        return (
            self.doc.time(entry.get("in"), default=0, what="entry in"),
            self.doc.time(entry.get("out"), default=default_out, what="entry out"),
        )

    def check_editable(self, track: Track) -> None:
        mixes = [
            t for t in track.tractor.findall("transition") if not props(t).get("internal_added")
        ]
        if mixes or (track.other is not None and any(e.tag == "entry" for e in track.other)):
            raise ProjectError(
                "unsupported_structure",
                f"Track {track.label} uses same-track mixes; editing it is not supported yet.",
            )

    def _rebuild(self, track: Track, entries: list[tuple[int, ET.Element]]) -> None:
        """Rewrite layer 0 from (start, entry) pairs with blanks between (no trailing blank)."""
        for el in list(track.playlist):
            if el.tag in ("entry", "blank"):
                track.playlist.remove(el)
        pos = 0
        indent = "\n  "
        for start, entry in sorted(entries, key=lambda pair: pair[0]):
            if start > pos:
                blank = ET.SubElement(
                    track.playlist, "blank", {"length": frames_to_clock(start - pos, self.doc.rate)}
                )
                blank.tail = indent
            entry.tail = indent
            track.playlist.append(entry)
            source_in, source_out = self._entry_bounds(entry)
            pos = start + source_out - source_in + 1
        if len(track.playlist):
            track.playlist[-1].tail = "\n "
        out = frames_to_clock(max(pos - 1, 0), self.doc.rate)
        if pos:
            track.tractor.set("out", out)
        elif "out" in track.tractor.attrib:
            del track.tractor.attrib["out"]

    def placed(self, track: Track) -> list[tuple[int, ET.Element]]:
        return [(i.start, i.element) for i in self.items(track) if not i.is_blank]

    def free(self, track: Track, start: int, end: int, ignore: set[int] | None = None) -> bool:
        ignore = ignore or set()
        return all(
            i.is_blank or id(i.element) in ignore or i.end <= start or i.start >= end
            for i in self.items(track)
        )

    # -- references --------------------------------------------------------------------
    def resolve_ref(self, ref: str) -> tuple[Track, Item]:
        match = re.fullmatch(r"(\d+):(\d+):(\d+)", ref.strip())
        if not match:
            raise ProjectError(
                "invalid_argument", f"Invalid clip ref {ref!r} (expected track:layer:entry)."
            )
        index, layer, entry_index = (int(g) for g in match.groups())
        track = next((t for t in self.tracks.values() if t.index == index), None)
        if track is None or layer != 0:
            raise ProjectError("clip_not_found", f"No clip {ref!r} on an editable track layer.")
        elements = [el for el in track.playlist if el.tag in ("entry", "blank")]
        if entry_index >= len(elements) or elements[entry_index].tag != "entry":
            raise ProjectError("clip_not_found", f"No clip at {ref!r}.")
        target = elements[entry_index]
        item = next(i for i in self.items(track) if i.element is target)
        return track, item

    # -- groups -------------------------------------------------------------------------
    def _groups(self) -> list[dict[str, Any]]:
        raw = props(self.sequence).get("kdenlive:sequenceproperties.groups", "").strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProjectError(
                "unsupported_structure", "Clip groups data is not valid JSON."
            ) from exc
        return data if isinstance(data, list) else []

    def _save_groups(self, groups: list[dict[str, Any]]) -> None:
        _set_prop(
            self.sequence, "kdenlive:sequenceproperties.groups", json.dumps(groups, indent=4) + "\n"
        )

    def partners(self, track: Track, item: Item) -> list[tuple[Track, Item]]:
        """The clip and, if it is in an AVSplit group, its counterpart."""
        key = f"{track.index - 1}:{item.start}"
        for group in self._groups():
            leaves = group.get("children", [])
            datas = [leaf.get("data", "") for leaf in leaves if isinstance(leaf, dict)]
            if not any(isinstance(d, str) and d.startswith(key + ":") for d in datas):
                continue
            if group.get("type") != "AVSplit" or any(
                isinstance(leaf, dict) and leaf.get("type") != "Leaf" for leaf in leaves
            ):
                raise ProjectError(
                    "unsupported_structure",
                    "This clip is in a group other than an audio/video pair.",
                    "Ungroup it in Kdenlive first.",
                )
            result = []
            for data in datas:
                t_index, pos = (int(x) for x in str(data).split(":")[:2])
                other_track = next(t for t in self.tracks.values() if t.index == t_index + 1)
                other_item = next(
                    (i for i in self.items(other_track) if not i.is_blank and i.start == pos), None
                )
                if other_item is None:
                    raise ProjectError(
                        "unsupported_structure", "Clip groups do not match the timeline content."
                    )
                result.append((other_track, other_item))
            return result
        return [(track, item)]

    def regroup(
        self,
        old: list[tuple[Track, int]],
        new: list[tuple[Track, int]] | None,
        extra: list[list[tuple[Track, int]]] | None = None,
    ) -> None:
        """Replace the group containing `old` leaves by `new` (None removes it); add `extra`."""
        groups = self._groups()
        old_keys = {f"{t.index - 1}:{pos}:" for t, pos in old}
        kept = [
            g
            for g in groups
            if not any(
                isinstance(leaf, dict)
                and any(str(leaf.get("data", "")).startswith(k) for k in old_keys)
                for leaf in g.get("children", [])
            )
        ]
        for leaves in ([new] if new and len(new) > 1 else []) + [
            e for e in (extra or []) if len(e) > 1
        ]:
            kept.append(
                {
                    "children": [
                        {"data": f"{t.index - 1}:{pos}:-1", "leaf": "clip", "type": "Leaf"}
                        for t, pos in leaves
                    ],
                    "type": "AVSplit",
                }
            )
        if kept or groups:
            self._save_groups(kept)

    # -- durations ----------------------------------------------------------------------
    def refresh_durations(self) -> int:
        end = 0
        for track in self.tracks.values():
            items = [i for i in self.items(track) if not i.is_blank]
            if items:
                end = max(end, items[-1].end)
        rate = self.doc.rate
        out = frames_to_clock(max(end - 1, 0), rate)
        self.sequence.set("out", out)
        _set_prop(self.sequence, "kdenlive:duration", frames_to_clock(end, rate))
        _set_prop(self.sequence, "kdenlive:maxduration", str(end))
        tractors = [el for el in self.edit.root if el.tag == "tractor"]
        project_tractor = tractors[-1]
        if props(project_tractor).get("kdenlive:projectTractor"):
            for track_ref in project_tractor.findall("track"):
                if track_ref.get("producer") == self.sequence.get("id"):
                    track_ref.set("out", out)
                    project_tractor.set("out", out)
        for entry in self.doc.main_bin.findall("entry"):
            if entry.get("producer") == self.sequence.get("id"):
                entry.set("out", out)
        return end


# ---------------------------------------------------------------- operations ---------
def _open(
    edit: EditableProject, revision: str | None, sequence: str | None, needs_revision: bool
) -> Timeline:
    if needs_revision and not revision:
        raise ProjectError(
            "revision_required",
            "Pass `revision`: the file sha256 from the get_timeline call that gave the clip ref.",
        )
    if revision and revision != edit.sha256:
        raise ProjectError(
            "stale_revision",
            "The project changed since `revision` was read; clip refs may point elsewhere.",
            "Call get_timeline again and retry.",
        )
    return Timeline(edit, _sequence(edit, sequence))


def _placement(tl: Timeline, track: Track, start: int, entry: ET.Element) -> ClipPlacement:
    source_in, source_out = tl._entry_bounds(entry)
    return ClipPlacement(
        track=track.label,
        start=start,
        end=start + source_out - source_in + 1,
        source_in=source_in,
        source_out=source_out,
    )


def _has_keyframes(entry: ET.Element) -> bool:
    return any(
        KEYFRAMES.search(p.text or "")
        for f in entry.findall("filter")
        for p in f.findall("property")
        if p.get("name") not in ("mlt_service", "kdenlive_id", "kdenlive:collapsed")
    )


def _bin_clip(tl: Timeline, bin_id: str) -> tuple[ET.Element, int, bool, bool]:
    bin_chain = next(
        (
            el
            for el in tl.edit.root
            if el.tag in ("chain", "producer")
            and props(el).get("kdenlive:id") == str(bin_id)
            and any(e.get("producer") == el.get("id") for e in tl.doc.main_bin.findall("entry"))
        ),
        None,
    )
    if bin_chain is None:
        raise ProjectError("clip_not_found", f"No bin clip with id {bin_id!r}.")
    bp = props(bin_chain)
    return (
        bin_chain,
        int(bp.get("length", "0") or 0),
        int(bp.get("video_index", "-1") or -1) >= 0,
        int(bp.get("audio_index", "-1") or -1) >= 0,
    )


def _insert_targets(
    tl: Timeline,
    bin_id: str,
    track_label: str,
    audio_track: str | None,
    has_video: bool,
    has_audio: bool,
) -> list[Track]:
    track = tl.tracks.get(track_label)
    if track is None:
        raise ProjectError(
            "track_not_found", f"No track {track_label!r}; tracks: {list(tl.tracks)}."
        )
    if (track.kind == "video" and not has_video) or (track.kind == "audio" and not has_audio):
        raise ProjectError(
            "incompatible_track", f"Clip {bin_id} has no {track.kind} to put on {track_label}."
        )
    targets = [track]
    if track.kind == "video" and has_audio and audio_track:
        audio = tl.tracks.get(audio_track)
        if audio is None or audio.kind != "audio":
            raise ProjectError(
                "track_not_found",
                f"No audio track {audio_track!r} for the clip's audio.",
                "Pass audio_track=null to insert the video only.",
            )
        targets.append(audio)
    return targets


def insert_clip(
    edit: EditableProject,
    bin_id: str,
    track_label: str,
    start: int,
    source_in: int = 0,
    source_out: int | None = None,
    audio_track: str | None = "A1",
    sequence: str | None = None,
) -> TimelineEditResult:
    tl = _open(edit, None, sequence, needs_revision=False)
    if start < 0:
        raise ProjectError("invalid_argument", "start must be >= 0.")
    bin_chain, length, has_video, has_audio = _bin_clip(tl, bin_id)
    if source_out is None:
        source_out = length - 1
    if not 0 <= source_in <= source_out < length:
        raise ProjectError(
            "invalid_range",
            f"Source range {source_in}..{source_out} is outside the clip (0..{length - 1}).",
        )
    targets = _insert_targets(tl, bin_id, track_label, audio_track, has_video, has_audio)
    end = start + source_out - source_in + 1
    for t in targets:
        tl.check_editable(t)
        if not tl.free(t, start, end):
            raise ProjectError(
                "overlap",
                f"{t.label} is not free between frames {start} and {end}.",
                "Move or trim the clips in the way first; nothing is overwritten.",
            )
    rate = tl.doc.rate
    placements, leaves = [], []
    for t in targets:
        chain = copy.deepcopy(bin_chain)
        chain.set("id", _next_id(edit.root, "chain"))
        chain.attrib.pop("in", None)
        for p in list(chain.findall("property")):
            if p.get("name") in ("set.test_audio", "set.test_image", "kdenlive:monitorPosition"):
                chain.remove(p)
        if has_audio and has_video:
            ET.SubElement(chain, "property", {"name": "set.test_audio"}).text = (
                "0" if t.kind == "audio" else "1"
            )
            ET.SubElement(chain, "property", {"name": "set.test_image"}).text = (
                "1" if t.kind == "audio" else "0"
            )
        chain.tail = "\n "
        edit.root.insert(list(edit.root).index(t.playlist), chain)
        tl.doc.by_id[chain.get("id", "")] = chain
        entry = ET.Element(
            "entry",
            {
                "in": frames_to_clock(source_in, rate),
                "out": frames_to_clock(source_out, rate),
                "producer": chain.get("id", ""),
            },
        )
        ET.SubElement(entry, "property", {"name": "kdenlive:id"}).text = str(bin_id)
        tl._rebuild(t, [*tl.placed(t), (start, entry)])
        placements.append(_placement(tl, t, start, entry))
        leaves.append((t, start))
    if len(leaves) == 2:
        tl.regroup([], None, extra=[leaves])
    duration = tl.refresh_durations()
    edit.changes.append(
        f"timeline: inserted clip {bin_id} on {', '.join(t.label for t in targets)} at {start}"
    )
    return TimelineEditResult(
        operation="insert", clips=placements, sequence_duration_frames=duration
    )


def move_clip(
    edit: EditableProject,
    ref: str,
    revision: str,
    start: int,
    track_label: str | None = None,
    sequence: str | None = None,
) -> TimelineEditResult:
    tl = _open(edit, revision, sequence, needs_revision=True)
    track, item = tl.resolve_ref(ref)
    if start < 0:
        raise ProjectError("invalid_argument", "start must be >= 0.")
    group = tl.partners(track, item)
    delta = start - item.start
    moves: list[tuple[Track, Item, Track]] = []
    for t, i in group:
        destination = t
        if t is track and track_label and track_label != track.label:
            found = tl.tracks.get(track_label)
            if found is None:
                raise ProjectError("track_not_found", f"No track {track_label!r}.")
            destination = found
            if destination.kind != t.kind:
                raise ProjectError(
                    "incompatible_track", f"Cannot move a {t.kind} clip to {destination.label}."
                )
        tl.check_editable(t)
        tl.check_editable(destination)
        moves.append((t, i, destination))
    ignore = {id(i.element) for _, i, _ in moves}
    for _t, i, destination in moves:
        if i.start + delta < 0:
            raise ProjectError(
                "invalid_argument", "The move would put a grouped clip before frame 0."
            )
        if not tl.free(destination, i.start + delta, i.end + delta, ignore):
            raise ProjectError(
                "overlap",
                f"{destination.label} is not free between frames "
                f"{i.start + delta} and {i.end + delta}.",
            )
    for t, i, _destination in moves:
        tl._rebuild(t, [(s, e) for s, e in tl.placed(t) if e is not i.element])
    placements, new_leaves = [], []
    for _t, i, destination in moves:
        tl._rebuild(destination, [*tl.placed(destination), (i.start + delta, i.element)])
        placements.append(_placement(tl, destination, i.start + delta, i.element))
        new_leaves.append((destination, i.start + delta))
    tl.regroup([(t, i.start) for t, i, _ in moves], new_leaves)
    duration = tl.refresh_durations()
    edit.changes.append(f"timeline: moved {ref} (+{len(moves) - 1} grouped) to frame {start}")
    return TimelineEditResult(operation="move", clips=placements, sequence_duration_frames=duration)


def remove_clip(
    edit: EditableProject, ref: str, revision: str, sequence: str | None = None
) -> TimelineEditResult:
    tl = _open(edit, revision, sequence, needs_revision=True)
    track, item = tl.resolve_ref(ref)
    group = tl.partners(track, item)
    for t, i in group:
        tl.check_editable(t)
        tl._rebuild(t, [(s, e) for s, e in tl.placed(t) if e is not i.element])
    tl.regroup([(t, i.start) for t, i in group], None)
    duration = tl.refresh_durations()
    edit.changes.append(
        f"timeline: removed {ref} (+{len(group) - 1} grouped); neighbours not moved"
    )
    return TimelineEditResult(operation="remove", clips=[], sequence_duration_frames=duration)


def trim_clip(
    edit: EditableProject,
    ref: str,
    revision: str,
    start: int | None = None,
    end: int | None = None,
    sequence: str | None = None,
) -> TimelineEditResult:
    """Set new timeline bounds [start, end); the source range follows (like Kdenlive resize)."""
    tl = _open(edit, revision, sequence, needs_revision=True)
    track, item = tl.resolve_ref(ref)
    new_start = item.start if start is None else start
    new_end = item.end if end is None else end
    if new_end <= new_start or new_start < 0:
        raise ProjectError(
            "invalid_range", "A clip must keep at least one frame: need 0 <= start < end."
        )
    group = tl.partners(track, item)
    plan = []
    for t, i in group:
        tl.check_editable(t)
        source_in, source_out = tl._entry_bounds(i.element)
        producer = tl.doc.by_id.get(i.element.get("producer") or "")
        length = int(props(producer).get("length", "0") or 0) if producer is not None else 0
        new_in = source_in + (new_start - item.start)
        new_out = source_out + (new_end - item.end)
        if new_in < 0 or (length and new_out >= length):
            raise ProjectError(
                "invalid_range",
                f"The source has frames 0..{length - 1}; trimming to "
                f"{new_in}..{new_out} is not possible.",
            )
        if new_start != item.start and _has_keyframes(i.element):
            raise ProjectError(
                "unsupported_structure",
                "Trimming the start of a clip with animated "
                "effects would shift its keyframes; not supported yet.",
            )
        if not tl.free(t, new_start, new_end, {id(i.element)}):
            raise ProjectError(
                "overlap", f"{t.label} is not free between frames {new_start} and {new_end}."
            )
        plan.append((t, i, new_in, new_out))
    rate = tl.doc.rate
    placements = []
    for t, i, new_in, new_out in plan:
        before = tl.placed(t)  # positions must be read before the entry's bounds change
        i.element.set("in", frames_to_clock(new_in, rate))
        i.element.set("out", frames_to_clock(new_out, rate))
        tl._rebuild(t, [(new_start if e is i.element else s, e) for s, e in before])
        placements.append(_placement(tl, t, new_start, i.element))
    tl.regroup([(t, i.start) for t, i in group], [(t, new_start) for t, *_ in plan])
    duration = tl.refresh_durations()
    edit.changes.append(f"timeline: trimmed {ref} to [{new_start}, {new_end})")
    return TimelineEditResult(operation="trim", clips=placements, sequence_duration_frames=duration)


def split_clip(
    edit: EditableProject, ref: str, revision: str, at: int, sequence: str | None = None
) -> TimelineEditResult:
    """Cut a clip at timeline frame `at` (start < at < end) into two adjacent clips."""
    tl = _open(edit, revision, sequence, needs_revision=True)
    track, item = tl.resolve_ref(ref)
    if not item.start < at < item.end:
        raise ProjectError(
            "invalid_range", f"Cut point must be inside the clip ({item.start} < at < {item.end})."
        )
    group = tl.partners(track, item)
    rate = tl.doc.rate
    placements, right_leaves = [], []
    for t, i in group:
        tl.check_editable(t)
        if _has_keyframes(i.element):
            raise ProjectError(
                "unsupported_structure",
                "Splitting a clip with animated effects is not supported yet.",
            )
    for t, i in group:
        source_in, source_out = tl._entry_bounds(i.element)
        cut = source_in + (at - i.start)
        right = copy.deepcopy(i.element)
        chain = tl.doc.by_id.get(i.element.get("producer") or "")
        if chain is not None:
            new_chain = copy.deepcopy(chain)
            new_chain.set("id", _next_id(edit.root, "chain"))
            edit.root.insert(list(edit.root).index(chain) + 1, new_chain)
            tl.doc.by_id[new_chain.get("id", "")] = new_chain
            right.set("producer", new_chain.get("id", ""))
        before = tl.placed(t)  # positions must be read before the entry's bounds change
        i.element.set("out", frames_to_clock(cut - 1, rate))
        right.set("in", frames_to_clock(cut, rate))
        right.set("out", frames_to_clock(source_out, rate))
        tl._rebuild(t, [*before, (at, right)])
        placements += [_placement(tl, t, i.start, i.element), _placement(tl, t, at, right)]
        right_leaves.append((t, at))
    if len(group) > 1:
        tl.regroup([], None, extra=[right_leaves])
    duration = tl.refresh_durations()
    edit.changes.append(f"timeline: split {ref} at frame {at}")
    return TimelineEditResult(
        operation="split", clips=placements, sequence_duration_frames=duration
    )
