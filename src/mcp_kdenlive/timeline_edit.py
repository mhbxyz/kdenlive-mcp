"""Frame-accurate timeline operations: insert, move, trim, split, remove (#11).

Conventions (see docs/tools.md):

* positions are integer frames at the project rate; timeline ranges are ``[start, end)``,
  source ranges are inclusive ``source_in..source_out`` like MLT;
* nothing ripples: neighbouring clips never move, and an operation that would overlap another
  clip fails with ``[overlap]`` (there is no overwrite mode);
* clips are addressed by the ``ref`` returned by get_timeline (``V1@90``) together
  with the file ``revision`` (its sha256), so stale references are rejected;
* a clip grouped with its audio/video counterpart (Kdenlive ``AVSplit`` group) is moved, trimmed,
  split and removed together with it, like Kdenlive does by default;
* like Kdenlive 26.08, every timeline clip instance gets its own ``<chain>`` copied from the
  bin clip, with ``set.test_audio``/``set.test_image`` disabling the unused half of AV media.

Same-track mixes (crossfades, #61): a track has two layers (playlists). Kdenlive puts mixed
clips on alternate layers so that they overlap during the mix; the mix is a transition inside the
track tractor whose ``in`` is where the right clip starts and whose ``out`` is where the left
clip ends; ``kdenlive:mixcut`` counts the frames from the original cut to that end. Clips that
are not part of a mix can be inserted, moved, trimmed, split and removed as usual; a mixed clip
keeps its mix: it cannot be moved, its mixed side cannot be trimmed, and it cannot be split
inside the mix. Removing a mixed clip removes its mixes and trims the partner back to the
original cut, like deleting the mix in Kdenlive. A clip left on layer 1 without a mix goes back
to layer 0.

Refused (``[unsupported_structure]``): same-track transitions that are not Kdenlive mixes,
groups other than AV pairs, moving a mixed clip, and splitting or trimming the start of clips
whose effects are animated (keyframes would shift).
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
from mcp_kdenlive.clip_effects import END_FADES, FADE_PAIRS, START_FADES, fade_length
from mcp_kdenlive.edits import _next_id, _sequence, _set_prop
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import props
from mcp_kdenlive.timecode import frames_to_clock
from mcp_kdenlive.writer import EditableProject

KEYFRAMES = re.compile(r"(^|;)\s*-?[\d:.]+\s*[~|!]?=")


class ClipPlacement(BaseModel):
    ref: str = Field(description="Ref of the clip after the edit, e.g. 'V1@90'.")
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
    layer: int = 0

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
    layers: list[ET.Element]  # playlists: 0 main, 1 used by same-track mixes

    @property
    def playlist(self) -> ET.Element:
        return self.layers[0]


@dataclass
class Mix:
    """A same-track mix: `left` ends at `end`, `right` starts at `start`, on other layers."""

    element: ET.Element
    start: int
    end: int
    cut: int
    left: Item
    right: Item

    def involves(self, item: Item) -> bool:
        return item.element is self.left.element or item.element is self.right.element

    def at_end_of(self, item: Item) -> bool:
        """True when the mix is at the end of `item` (it is the left clip)."""
        return item.element is self.left.element

    def partner(self, item: Item) -> Item:
        return self.right if item.element is self.left.element else self.left

    def describe(self, label: str) -> str:
        return f"mix (crossfade) on {label} at frames {self.start}-{self.end}"


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
                [layer for layer in layers[:2] if layer is not None],
            )

    # -- playlist helpers ------------------------------------------------------------
    def items(self, track: Track, layer: int = 0) -> list[Item]:
        """Entries and blanks of one layer, with their timeline positions."""
        out: list[Item] = []
        if layer >= len(track.layers):
            return out
        pos = 0
        for el in track.layers[layer]:
            if el.tag == "blank":
                length = self.doc.time(el.get("length"), what="blank length")
            elif el.tag == "entry":
                length = self._entry_bounds(el)[1] - self._entry_bounds(el)[0] + 1
            else:
                continue
            out.append(Item(el, pos, length, layer))
            pos += length
        return out

    def clips(self, track: Track) -> list[Item]:
        """Clips of both layers, by position."""
        found = [i for layer in range(len(track.layers)) for i in self.items(track, layer)]
        return sorted((i for i in found if not i.is_blank), key=lambda i: (i.start, i.layer))

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

    def set_bounds(self, track: Track, item: Item, start: int, end: int) -> None:
        """Resize a clip to [start, end) on its layer; the source range follows."""
        source_in, source_out = self._entry_bounds(item.element)
        before = self.placed(track, item.layer)  # read positions before the bounds change
        rate = self.doc.rate
        item.element.set("in", frames_to_clock(source_in + start - item.start, rate))
        item.element.set("out", frames_to_clock(source_out + end - item.end, rate))
        self._rebuild(
            track, [(start if e is item.element else s, e) for s, e in before], item.layer
        )

    # -- mixes ---------------------------------------------------------------------------
    def mixes(self, track: Track) -> list[Mix]:
        """The track's same-track mixes; anything else inside the track is refused."""
        clips = self.clips(track)
        found = []
        for transition in track.tractor.findall("transition"):
            p = props(transition)
            if p.get("internal_added"):
                continue
            start = self.doc.time(transition.get("in"), default=-1, what="mix in")
            end = self.doc.time(transition.get("out"), default=-1, what="mix out")
            cut_offset = p.get("kdenlive:mixcut", "")
            # Which layer holds the left clip: video mixes swap a_track/b_track, audio mixes
            # set reverse (docs/research/mixes.md).
            left_layer = int((p.get("a_track") == "1") != (p.get("reverse") == "1"))
            left = next((c for c in clips if c.end == end and c.layer == left_layer), None)
            right = next((c for c in clips if c.start == start and c.layer != left_layer), None)
            if left is None or right is None or not cut_offset.lstrip("-").isdigit():
                raise ProjectError(
                    "unsupported_structure",
                    f"Track {track.label} has a same-track transition ({p.get('mlt_service')}, "
                    f"frames {start}-{end}) that is not a Kdenlive mix of two clips.",
                )
            found.append(Mix(transition, start, end, end - int(cut_offset), left, right))
        return found

    def mixes_of(self, track: Track, item: Item) -> list[Mix]:
        return [mix for mix in self.mixes(track) if mix.involves(item)]

    def check_editable(self, track: Track) -> None:
        self.mixes(track)

    def settle(self, track: Track) -> None:
        """Move clips left on layer 1 without a mix back to layer 0 (Kdenlive's layout)."""
        if len(track.layers) < 2:
            return
        mixed = {id(x.element) for m in self.mixes(track) for x in (m.left, m.right)}
        for item in [i for i in self.items(track, 1) if not i.is_blank]:
            if id(item.element) in mixed or not self.free(
                track, item.start, item.end, {id(item.element)}
            ):
                continue
            self._rebuild(
                track, [(s, e) for s, e in self.placed(track, 1) if e is not item.element], 1
            )
            self._rebuild(track, [*self.placed(track, 0), (item.start, item.element)], 0)

    # -- layout ----------------------------------------------------------------------------
    def _rebuild(self, track: Track, entries: list[tuple[int, ET.Element]], layer: int = 0) -> None:
        """Rewrite one layer from (start, entry) pairs with blanks between (no trailing blank)."""
        playlist = track.layers[layer]
        for el in list(playlist):
            if el.tag in ("entry", "blank"):
                playlist.remove(el)
        pos = 0
        indent = "\n  "
        for start, entry in sorted(entries, key=lambda pair: pair[0]):
            if start > pos:
                blank = ET.SubElement(
                    playlist, "blank", {"length": frames_to_clock(start - pos, self.doc.rate)}
                )
                blank.tail = indent
            entry.tail = indent
            playlist.append(entry)
            source_in, source_out = self._entry_bounds(entry)
            pos = start + source_out - source_in + 1
        if len(playlist):
            playlist[-1].tail = "\n "
        end = max((i.end for i in self.clips(track)), default=0)
        if end:
            track.tractor.set("out", frames_to_clock(end - 1, self.doc.rate))
        elif "out" in track.tractor.attrib:
            del track.tractor.attrib["out"]

    def placed(self, track: Track, layer: int = 0) -> list[tuple[int, ET.Element]]:
        return [(i.start, i.element) for i in self.items(track, layer) if not i.is_blank]

    def free(self, track: Track, start: int, end: int, ignore: set[int] | None = None) -> bool:
        """No clip of either layer (except `ignore`d ones) in [start, end)."""
        ignore = ignore or set()
        return all(
            id(i.element) in ignore or i.end <= start or i.start >= end for i in self.clips(track)
        )

    # -- references --------------------------------------------------------------------
    def resolve_ref(self, ref: str) -> tuple[Track, Item]:
        """A clip from its ref: '<track>@<start>[#layer]' (get_timeline) or legacy
        'index:layer:entry' (0.1)."""
        text = ref.strip()
        if match := re.fullmatch(r"([AV]\d+)@(\d+)(?:#([01]))?", text):
            label, start, layer = match.group(1), int(match.group(2)), match.group(3)
            track = self.tracks.get(label)
            found = [
                i
                for i in (self.clips(track) if track else [])
                if i.start == start and (layer is None or i.layer == int(layer))
            ]
            if len(found) > 1:
                raise ProjectError(
                    "invalid_argument",
                    f"Two clips start at {start} on {label}; add '#0' or '#1' for the layer.",
                )
            if not found or track is None:
                raise ProjectError(
                    "clip_not_found",
                    f"No clip {ref!r}.",
                    "Read the refs from get_timeline (they name the track and start frame).",
                )
            return track, found[0]
        match = re.fullmatch(r"(\d+):(\d+):(\d+)", text)
        if not match:
            raise ProjectError(
                "invalid_argument", f"Invalid clip ref {ref!r} (expected e.g. 'V1@90')."
            )
        index, layer_index, entry_index = (int(g) for g in match.groups())
        legacy = next((t for t in self.tracks.values() if t.index == index), None)
        if legacy is None or layer_index >= len(legacy.layers):
            raise ProjectError("clip_not_found", f"No clip {ref!r} on an editable track layer.")
        elements = [el for el in legacy.layers[layer_index] if el.tag in ("entry", "blank")]
        if entry_index >= len(elements) or elements[entry_index].tag != "entry":
            raise ProjectError("clip_not_found", f"No clip at {ref!r}.")
        target = elements[entry_index]
        item = next(i for i in self.items(legacy, layer_index) if i.element is target)
        return legacy, item

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
        """The clip and, if it is in an AVSplit group, its counterpart.

        A clip inside any other group (a user group made with Ctrl+G, possibly containing an
        audio/video pair) is refused: Kdenlive would move the whole group.
        """
        key = f"{track.index - 1}:{item.start}:"

        def path_to(node: dict[str, Any], trail: list[dict[str, Any]]) -> list[dict[str, Any]]:
            for child in node.get("children", []):
                if not isinstance(child, dict):
                    continue
                if str(child.get("data", "")).startswith(key):
                    return [*trail, node]
                found = path_to(child, [*trail, node])
                if found:
                    return found
            return []

        for group in self._groups():
            trail = path_to(group, []) if isinstance(group, dict) else []
            if not trail:
                continue
            innermost = trail[-1]
            if any(g.get("type") != "AVSplit" for g in trail) or any(
                isinstance(leaf, dict) and leaf.get("type") != "Leaf"
                for leaf in innermost.get("children", [])
            ):
                raise ProjectError(
                    "unsupported_structure",
                    "This clip is in a group other than an audio/video pair.",
                    "Ungroup it in Kdenlive first.",
                )
            result = []
            for leaf in innermost.get("children", []):
                t_index, pos = (int(x) for x in str(leaf.get("data", "")).split(":")[:2])
                other_track = next(t for t in self.tracks.values() if t.index == t_index + 1)
                found = [i for i in self.clips(other_track) if i.start == pos]
                if other_track is track and item in found:
                    found = [item]
                if len(found) != 1:
                    raise ProjectError(
                        "unsupported_structure",
                        "Clip groups do not match the timeline content"
                        + (
                            f": two clips start at frame {pos} on {other_track.label}."
                            if found
                            else "."
                        ),
                    )
                result.append((other_track, found[0]))
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

    def rekey(self, moved: dict[tuple[int, int], int]) -> None:
        """Point group leaves at new start positions: {(track index, old start): new start}."""
        if not moved:
            return
        groups = self._groups()

        def walk(node: dict[str, Any]) -> None:
            data = node.get("data")
            if isinstance(data, str) and data.count(":") >= 2:
                track_index, pos, rest = data.split(":", 2)
                new = moved.get((int(track_index) + 1, int(pos)))
                if new is not None:
                    node["data"] = f"{track_index}:{new}:{rest}"
            for child in node.get("children", []):
                if isinstance(child, dict):
                    walk(child)

        for group in groups:
            walk(group)
        if groups:
            self._save_groups(groups)

    # -- durations ----------------------------------------------------------------------
    def refresh_durations(self) -> int:
        end = 0
        for track in self.tracks.values():
            end = max([end, *(i.end for i in self.clips(track))])
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
        ref=f"{track.label}@{start}",
        track=track.label,
        start=start,
        end=start + source_out - source_in + 1,
        source_in=source_in,
        source_out=source_out,
    )


def _has_keyframes(entry: ET.Element) -> bool:
    """True when an effect parameter is animated (two keyframes or more). Edge fades are not:
    their keyframes are relative to the clip's edges and follow trims and splits."""
    return any(
        KEYFRAMES.search(p.text or "") and len([k for k in (p.text or "").split(";") if k]) > 1
        for f in entry.findall("filter")
        if props(f).get("kdenlive_id") not in START_FADES + END_FADES
        for p in f.findall("property")
        if p.get("name") not in ("mlt_service", "kdenlive_id", "kdenlive:collapsed")
    )


def anchor_fades(
    tl: Timeline,
    entry: ET.Element,
    lengths: dict[str, int] | None = None,
    only: set[str] | None = None,
) -> None:
    """Keep edge fades at the clip's edges after its source range changed (trim, split).

    Fade filters store source frames (see audio.py). `lengths` overrides a fade's length by
    kdenlive_id; `only` limits the fades that are rewritten. A fade-in keeps at most the whole
    clip, and its fade-out at most what the fade-in leaves.
    """
    source_in, source_out = tl._entry_bounds(entry)
    clip_length = source_out - source_in + 1
    rate = tl.doc.rate
    wanted = {
        kid: (lengths or {}).get(kid, fade_length(entry, kid, tl.doc.time))
        for kid in START_FADES + END_FADES
    }
    for start_id, end_id in FADE_PAIRS.items():
        wanted[start_id] = min(wanted[start_id], clip_length)
        wanted[end_id] = min(wanted[end_id], clip_length - wanted[start_id])
    for flt in entry.findall("filter"):
        kdenlive_id = props(flt).get("kdenlive_id", "")
        if kdenlive_id not in wanted or (only is not None and kdenlive_id not in only):
            continue
        length = wanted[kdenlive_id]
        if kdenlive_id in START_FADES:
            flt.set("in", frames_to_clock(source_in, rate))
            flt.set("out", frames_to_clock(source_in + length, rate))
        else:
            flt.set("in", frames_to_clock(max(source_out - length, source_in), rate))
            flt.set("out", frames_to_clock(source_out, rate))


def _drop_fades(entry: ET.Element, kdenlive_ids: tuple[str, ...]) -> None:
    for flt in list(entry.findall("filter")):
        if props(flt).get("kdenlive_id") in kdenlive_ids:
            entry.remove(flt)


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
        if mixed := tl.mixes_of(t, i):
            raise ProjectError(
                "unsupported_structure",
                f"The clip on {t.label} at {i.start}-{i.end} is part of a "
                f"{mixed[0].describe(t.label)}; moving it would break the mix.",
                "Trim, split or remove clips around the mix instead, or remove the mix in "
                "Kdenlive first.",
            )
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
        tl._rebuild(t, [(s, e) for s, e in tl.placed(t, i.layer) if e is not i.element], i.layer)
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
    for t, _i in group:
        tl.check_editable(t)
    moved: dict[tuple[int, int], int] = {}
    placements, unmixed = [], 0
    for t, i in group:
        mixes = tl.mixes_of(t, i)
        for mix in mixes:
            t.tractor.remove(mix.element)
        tl._rebuild(t, [(s, e) for s, e in tl.placed(t, i.layer) if e is not i.element], i.layer)
        # Like deleting the mix in Kdenlive: the partner goes back to the original cut.
        for mix in mixes:
            other = mix.partner(i)
            current = next(c for c in tl.clips(t) if c.element is other.element)
            start, end = (mix.cut, current.end) if other is mix.right else (current.start, mix.cut)
            tl.set_bounds(t, current, start, end)
            if start != current.start:
                moved[(t.index, current.start)] = start
            placements.append(_placement(tl, t, start, current.element))
            unmixed += 1
        tl.settle(t)
    tl.regroup([(t, i.start) for t, i in group], None)
    tl.rekey(moved)
    duration = tl.refresh_durations()
    edit.changes.append(
        f"timeline: removed {ref} (+{len(group) - 1} grouped); neighbours not moved"
        + (f"; {unmixed} mix(es) removed, partners trimmed back to the cut" if unmixed else "")
    )
    return TimelineEditResult(
        operation="remove", clips=placements, sequence_duration_frames=duration
    )


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
    # Grouped clips move their edges by the same amounts, even when their bounds differ.
    d_start, d_end = new_start - item.start, new_end - item.end
    plan = []
    for t, i in group:
        tl.check_editable(t)
        p_start, p_end = i.start + d_start, i.end + d_end
        if p_end <= p_start or p_start < 0:
            raise ProjectError(
                "invalid_range", f"The grouped clip on {t.label} would have no frame left."
            )
        source_in, source_out = tl._entry_bounds(i.element)
        producer = tl.doc.by_id.get(i.element.get("producer") or "")
        length = int(props(producer).get("length", "0") or 0) if producer is not None else 0
        if source_in + d_start < 0 or (length and source_out + d_end >= length):
            raise ProjectError(
                "invalid_range",
                f"The source has frames 0..{length - 1}; trimming to "
                f"{source_in + d_start}..{source_out + d_end} is not possible.",
            )
        if d_start and _has_keyframes(i.element):
            raise ProjectError(
                "unsupported_structure",
                "Trimming the start of a clip with animated "
                "effects would shift its keyframes; not supported yet.",
            )
        for mix in tl.mixes_of(t, i):
            at_end = mix.at_end_of(i)
            if d_end if at_end else d_start:
                raise ProjectError(
                    "unsupported_structure",
                    f"The {'end' if at_end else 'start'} of the clip on {t.label} is in a "
                    f"{mix.describe(t.label)}; trimming that side would break the mix.",
                    "Trim the other side, or remove the mix in Kdenlive first.",
                )
            # Keep at least one frame outside the mix so partners never share an edge.
            if (p_start >= mix.start) if at_end else (p_end <= mix.end):
                raise ProjectError(
                    "unsupported_structure",
                    f"The clip on {t.label} must extend beyond its {mix.describe(t.label)}.",
                )
        ignore = {id(i.element)}
        if (p_start < i.start and not tl.free(t, p_start, i.start, ignore)) or (
            p_end > i.end and not tl.free(t, i.end, p_end, ignore)
        ):
            raise ProjectError(
                "overlap", f"{t.label} is not free between frames {p_start} and {p_end}."
            )
        plan.append((t, i, p_start, p_end))
    placements = []
    for t, i, p_start, p_end in plan:
        tl.set_bounds(t, i, p_start, p_end)
        anchor_fades(tl, i.element)
        placements.append(_placement(tl, t, p_start, i.element))
    tl.regroup([(t, i.start) for t, i in group], [(t, p_start) for t, _i, p_start, _e in plan])
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
        if not i.start < at < i.end:
            raise ProjectError(
                "unsupported_structure",
                f"The grouped clip on {t.label} ({i.start}-{i.end}) does not span frame {at}.",
            )
        for mix in tl.mixes_of(t, i):
            if mix.start < at < mix.end:
                raise ProjectError(
                    "unsupported_structure",
                    f"Frame {at} is inside a {mix.describe(t.label)}; cut outside the mix.",
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
        before = tl.placed(t, i.layer)  # positions must be read before the bounds change
        i.element.set("out", frames_to_clock(cut - 1, rate))
        right.set("in", frames_to_clock(cut, rate))
        right.set("out", frames_to_clock(source_out, rate))
        # The cut is in the middle: the left half keeps the fade-in, the right half the fade-out.
        _drop_fades(i.element, END_FADES)
        _drop_fades(right, START_FADES)
        for flt in right.findall("filter"):
            flt.set("id", _next_id(edit.root, "filter"))
        anchor_fades(tl, i.element)
        anchor_fades(tl, right)
        tl._rebuild(t, [*before, (at, right)], i.layer)
        tl.settle(t)
        placements += [_placement(tl, t, i.start, i.element), _placement(tl, t, at, right)]
        right_leaves.append((t, at))
    if len(group) > 1:
        tl.regroup([], None, extra=[right_leaves])
    duration = tl.refresh_durations()
    edit.changes.append(f"timeline: split {ref} at frame {at}")
    return TimelineEditResult(
        operation="split", clips=placements, sequence_duration_frames=duration
    )


# ---------------------------------------------------------------- crossfades (#14) ----
LUMA_PROPS = (("factory", "loader"), ("resource", None), ("mlt_service", "luma"),
              ("kdenlive_id", "luma"))  # fmt: skip
LUMA_TAIL = (("softness", "0"), ("alpha_over", "0"), ("fix_background_alpha", "1"),
             ("invert", "0"))  # fmt: skip
MIX_PROPS = (("duck_threshold", "0"), ("duck_attenuation", "-12"), ("duck_level", "0"),
             ("duck_fade_in", "1500"), ("duck_fade_out", "250"), ("prefix", None),
             ("mlt_service", "mix"), ("kdenlive_id", "mix"))  # fmt: skip


def _next_clip(tl: Timeline, track: Track, item: Item) -> Item | None:
    return next((c for c in tl.clips(track) if c.start == item.end and c is not item), None)


def _handles(tl: Timeline, item: Item) -> tuple[int, int]:
    """(frames available before the clip's source in, after its source out)."""
    source_in, source_out = tl._entry_bounds(item.element)
    producer = tl.doc.by_id.get(item.element.get("producer") or "")
    length = int(props(producer).get("length", "0") or 0) if producer is not None else 0
    return source_in, max(length - 1 - source_out, 0) if length else 0


def _mix_transition(
    tl: Timeline, track: Track, left_layer: int, start: int, end: int, after: int
) -> ET.Element:
    rate = tl.doc.rate
    element = ET.Element(
        "transition",
        {"id": _next_id(tl.edit.root, "transition"), "in": frames_to_clock(start, rate),
         "out": frames_to_clock(end, rate)},
    )  # fmt: skip
    if track.kind == "video":
        a, b = ("1", "0") if left_layer else ("0", "1")
        values = [("a_track", a), ("b_track", b), *LUMA_PROPS, ("kdenlive:mixcut", str(after)),
                  *LUMA_TAIL, ("reverse", "0")]  # fmt: skip
    else:
        values = [
            ("a_track", "0"), ("b_track", "1"), *MIX_PROPS, ("kdenlive:mixcut", str(after)),
            ("start", "-1"), ("accepts_blanks", "1"), ("reverse", str(left_layer)),
        ]  # fmt: skip
    for name, value in values:
        prop = ET.SubElement(element, "property", {"name": name})
        prop.text = value
    element.tail = "\n "
    existing = track.tractor.findall("transition")
    anchor = next(
        (t for t in existing if tl.doc.time(t.get("in"), default=0, what="transition in") > start),
        None,
    )
    if anchor is not None:
        track.tractor.insert(list(track.tractor).index(anchor), element)
    else:
        track.tractor.append(element)
    return element


def add_mix(
    edit: EditableProject,
    ref: str,
    revision: str,
    duration: int | None = None,
    sequence: str | None = None,
) -> TimelineEditResult:
    """Crossfade the clip into the next one on its track (Kdenlive's *Mix Clips*).

    Like Kdenlive, the mix is centred on the cut when the handles allow, both clips are extended
    into their handles, and the right clip goes to the other layer. Grouped audio/video clips
    get the matching audio crossfade.
    """
    tl = _open(edit, revision, sequence, needs_revision=True)
    track, item = tl.resolve_ref(ref)
    frames = round(tl.doc.rate.num / tl.doc.rate.den) if duration is None else duration
    if frames < 2:
        raise ProjectError("invalid_argument", "A crossfade lasts at least 2 frames.")
    plan = []
    for t, left in tl.partners(track, item):
        tl.check_editable(t)
        existing = next((m for m in tl.mixes(t) if m.at_end_of(left)), None)
        if existing is not None:
            raise ProjectError(
                "invalid_argument",
                f"The clip on {t.label} already ends in a mix (frames "
                f"{existing.start}-{existing.end}).",
                "Use remove_mix first to change it.",
            )
        right = _next_clip(tl, t, left)
        if right is None:
            raise ProjectError(
                "invalid_argument",
                f"No clip starts where the clip on {t.label} ends (frame {left.end}).",
                "Move the next clip so it touches this one; a crossfade joins two adjacent clips.",
            )
        if any(m.right.element is right.element for m in tl.mixes(t)):
            raise ProjectError(
                "invalid_argument", f"There is already a mix at frame {left.end} on {t.label}."
            )
        head = _handles(tl, right)[0]
        tail = _handles(tl, left)[1]
        before = min(frames // 2, head)
        after = min(frames - before, tail)
        before = min(frames - after, head)
        if before + after < frames:
            raise ProjectError(
                "invalid_range",
                f"The clips on {t.label} have only {before + after} frames of handles for the "
                f"crossfade (need {frames}).",
                "Use a shorter crossfade, or trim the clips so their sources extend past the cut.",
            )
        if left.end - before <= left.start or right.start + after >= right.end:
            raise ProjectError(
                "invalid_range", f"A {frames}-frame crossfade is longer than a clip on {t.label}."
            )
        plan.append((t, left, right, before, after))
    moved: dict[tuple[int, int], int] = {}
    placements = []
    for t, left, right, before, after in plan:
        cut = left.end
        tl.set_bounds(t, left, left.start, left.end + after)
        current = next(c for c in tl.clips(t) if c.element is right.element)
        new_layer = 1 - left.layer
        tl._rebuild(
            t,
            [(s, e) for s, e in tl.placed(t, current.layer) if e is not right.element],
            current.layer,
        )
        source_in, _ = tl._entry_bounds(right.element)
        right.element.set("in", frames_to_clock(source_in - before, tl.doc.rate))
        if new_layer >= len(t.layers):
            raise ProjectError("unsupported_structure", f"Track {t.label} has no second layer.")
        tl._rebuild(t, [*tl.placed(t, new_layer), (cut - before, right.element)], new_layer)
        _mix_transition(tl, t, left.layer, cut - before, cut + after, after)
        moved[(t.index, right.start)] = cut - before
        placements += [
            _placement(tl, t, left.start, left.element),
            _placement(tl, t, cut - before, right.element),
        ]
    tl.rekey(moved)
    duration_frames = tl.refresh_durations()
    edit.changes.append(f"timeline: crossfade of {frames} frames after {ref}")
    return TimelineEditResult(
        operation="mix", clips=placements, sequence_duration_frames=duration_frames
    )


def remove_mix(
    edit: EditableProject, ref: str, revision: str, sequence: str | None = None
) -> TimelineEditResult:
    """Remove the crossfade at the end of the clip; both clips go back to the original cut."""
    tl = _open(edit, revision, sequence, needs_revision=True)
    track, item = tl.resolve_ref(ref)
    moved: dict[tuple[int, int], int] = {}
    placements = []
    for t, left in tl.partners(track, item):
        tl.check_editable(t)
        mix = next((m for m in tl.mixes(t) if m.at_end_of(left)), None)
        if mix is None:
            raise ProjectError(
                "invalid_argument", f"No crossfade at the end of the clip on {t.label}."
            )
        t.tractor.remove(mix.element)
        tl.set_bounds(t, left, left.start, mix.cut)
        right = next(c for c in tl.clips(t) if c.element is mix.right.element)
        tl.set_bounds(t, right, mix.cut, right.end)
        moved[(t.index, right.start)] = mix.cut
        tl.settle(t)
        placements += [
            _placement(tl, t, left.start, left.element),
            _placement(tl, t, mix.cut, right.element),
        ]
    tl.rekey(moved)
    duration_frames = tl.refresh_durations()
    edit.changes.append(f"timeline: removed the crossfade after {ref}")
    return TimelineEditResult(
        operation="unmix", clips=placements, sequence_duration_frames=duration_frames
    )
