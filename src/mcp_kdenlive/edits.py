"""Edits on a loaded project: bin media and tracks (#10). All edits go through writer.save.

Structures copied from Kdenlive 26.08.2 output (see create.py and tests/fixtures):

* a track is a ``<tractor>`` holding two ``<playlist>`` (the second one for same-track mixes);
  audio tracks carry ``kdenlive:audio_track=1``, ``hide="video"`` on both playlists and three
  disabled internal filters (volume, panner, audiolevel); video tracks use ``hide="audio"``;
* each track has an internal transition in the sequence tractor: ``mix`` for audio,
  ``qtblend`` (compositing 0) for video, from the background track (``a_track=0``);
* Kdenlive orders tracks bottom-up: audio tracks first, then video. New video tracks are added
  on top; new audio tracks at the bottom, which shifts every track index by one (transitions,
  compositions, clip groups and target tracks are renumbered accordingly).
"""

from __future__ import annotations

import json
import re
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import BaseModel, Field

from mcp_kdenlive import create, media
from mcp_kdenlive.kdenlive_doc import ProjectError, props
from mcp_kdenlive.media import _Resolver
from mcp_kdenlive.timecode import frames_to_clock
from mcp_kdenlive.writer import EditableProject

MAX_MEDIA_PER_CALL = 50
MAX_TRACKS = 64
SEQUENCE_INDEX_PROPS = (
    "kdenlive:sequenceproperties.activeTrack",
    "kdenlive:sequenceproperties.audioTarget",
    "kdenlive:sequenceproperties.videoTarget",
)


class MediaAdded(BaseModel):
    media: str
    bin_id: str
    status: str = Field(description="'added', or 'existing' when already in the bin.")
    reference: str | None = None
    duration_frames: int | None = None


class TrackAdded(BaseModel):
    label: str
    kind: str
    name: str | None
    index: int = Field(description="MLT index of the new track in the sequence.")


# ---------------------------------------------------------------- helpers ------------
def _prop(parent: ET.Element, name: str, value: str) -> ET.Element:
    el = ET.SubElement(parent, "property", {"name": name})
    el.text = value
    return el


def _set_prop(parent: ET.Element, name: str, value: str) -> None:
    for p in parent.findall("property"):
        if p.get("name") == name:
            p.text = value
            return
    # keep Kdenlive's habit of properties before child elements
    first_child = next((i for i, el in enumerate(parent) if el.tag != "property"), len(parent))
    el = ET.Element("property", {"name": name})
    el.text = value
    parent.insert(first_child, el)


def _next_id(root: ET.Element, prefix: str) -> str:
    numbers = [
        int(m.group(1))
        for el in root.iter()
        if el.get("id")
        for m in [re.fullmatch(rf"{prefix}(\d+)", el.get("id") or "")]
        if m
    ]
    return f"{prefix}{max(numbers, default=-1) + 1}"


def _next_bin_id(root: ET.Element) -> int:
    ids = []
    for el in root.iter():
        value = props(el).get("kdenlive:id") if el.tag in ("producer", "chain", "tractor") else None
        if value and value.isdigit():
            ids.append(int(value))
    return max(ids, default=3) + 1


def _sequence(project: EditableProject, sequence: str | None) -> ET.Element:
    tractors = project.doc.sequence_tractors()
    if sequence is None:
        active = project.doc.active_sequence_uuid()
        return next((t for t in tractors if props(t).get("kdenlive:uuid") == active), tractors[0])
    for t in tractors:
        p = props(t)
        if sequence in (p.get("kdenlive:uuid"), p.get("kdenlive:clipname"), t.get("id")):
            return t
    raise ProjectError("sequence_not_found", f"No sequence matches {sequence!r}.")


def _main_bin(project: EditableProject) -> ET.Element:
    return project.doc.main_bin


def _profile_file(project: EditableProject, directory: Path) -> Path:
    """Write the project's <profile> as an MLT profile file usable with melt -profile."""
    profile = project.root.find("profile")
    if profile is None:  # pragma: no cover - checked at load
        raise ProjectError("unsupported_structure", "project has no profile")
    path = directory / "project.profile"
    path.write_text("".join(f"{k}={v}\n" for k, v in profile.attrib.items()), encoding="utf-8")
    return path


# ---------------------------------------------------------------- bin media ----------
def add_media(project: EditableProject, media_files: list[str]) -> list[MediaAdded]:
    """Add audio/video files to the bin (not to the timeline).

    References are stored relative to the project's current root when the media is inside it
    (absolute otherwise); writer.save rebases them if the copy goes to another folder.
    """
    if not media_files:
        raise ProjectError("invalid_argument", "No media file given.")
    if len(media_files) > MAX_MEDIA_PER_CALL:
        raise ProjectError("invalid_argument", f"At most {MAX_MEDIA_PER_CALL} files per call.")
    resolver = _Resolver(project.doc)
    main_bin = _main_bin(project)
    existing: dict[str, str] = {}
    for entry in main_bin.findall("entry"):
        producer = project.doc.by_id.get(entry.get("producer") or "")
        if producer is None:
            continue
        p = props(producer)
        if p.get("resource") and p.get("kdenlive:id"):
            resolved, _ = resolver.resolve(p.get("kdenlive:originalurl") or p["resource"])
            existing[str(Path(resolved).resolve())] = p["kdenlive:id"]
    rate = project.doc.rate
    results: list[MediaAdded] = []
    with tempfile.TemporaryDirectory(prefix="kdenlive-mcp-add-") as tmp:
        profile_path = _profile_file(project, Path(tmp))
        for raw in media_files:
            checked = media.inspect_file(raw)
            if checked.status is not media.MediaStatus.OK:
                raise ProjectError(
                    "media_unreadable",
                    f"{raw}: {checked.status.value}"
                    + (f" ({checked.detail})" if checked.detail else ""),
                )
            path = Path(checked.path)
            key = str(path.resolve())
            if key in existing:
                results.append(MediaAdded(media=str(path), bin_id=existing[key], status="existing"))
                continue
            producer = create.melt_producer(path, str(profile_path))
            pp = props(producer)
            length = int(pp.get("length", "0") or 0)
            has_video = int(pp.get("video_index", "-1") or -1) >= 0
            has_audio = int(pp.get("audio_index", "-1") or -1) >= 0
            if length <= 0 or not (has_video or has_audio):
                raise ProjectError("media_unreadable", f"{path} has no playable audio or video.")
            bin_id = str(_next_bin_id(project.root))
            reference = create._media_reference(path, Path(resolver.effective).resolve())
            file_hash, size = create.kdenlive_file_hash(path)
            chain = create._chain(
                _next_id(project.root, "chain"),
                producer,
                frames_to_clock(length - 1, rate),
                {
                    "resource": reference,
                    "mlt_service": "avformat-novalidate" if has_video else "avformat",
                    "kdenlive:folderid": "-1",
                    "kdenlive:id": bin_id,
                    "kdenlive:control_uuid": "{" + str(uuid.uuid4()) + "}",
                    "kdenlive:clip_type": "0"
                    if (has_video and has_audio)
                    else ("1" if has_audio else "2"),
                    "kdenlive:file_size": str(size),
                    "kdenlive:file_hash": file_hash,
                    "eof": "pause",
                    "mute_on_pause": "0",
                    "length": str(length),
                },
            )
            chain.tail = "\n "
            project.root.insert(list(project.root).index(main_bin), chain)
            entry = ET.SubElement(
                main_bin,
                "entry",
                {
                    "in": frames_to_clock(0, rate),
                    "out": frames_to_clock(length - 1, rate),
                    "producer": chain.get("id", ""),
                },
            )
            entry.tail = "\n "
            project.doc.by_id[chain.get("id", "")] = chain
            existing[key] = bin_id
            results.append(
                MediaAdded(
                    media=str(path),
                    bin_id=bin_id,
                    status="added",
                    reference=reference,
                    duration_frames=length,
                )
            )
            project.changes.append(f"bin: added {path.name} as clip {bin_id}")
    return results


# ---------------------------------------------------------------- tracks -------------
def _track_targets(sequence: ET.Element) -> list[ET.Element]:
    return sequence.findall("track")


def _shift_indexes(project: EditableProject, sequence: ET.Element, first: int) -> None:
    """Every MLT track index >= `first` moves up by one (a track was inserted at `first`)."""
    for transition in sequence.findall("transition"):
        for prop in transition.findall("property"):
            if prop.get("name") in ("a_track", "b_track") and (prop.text or "").isdigit():
                value = int(prop.text or 0)
                if value >= first:
                    prop.text = str(value + 1)
    group_first = first - 1  # Kdenlive group/target indexes exclude the background track
    for prop in sequence.findall("property"):
        name = prop.get("name")
        if name in SEQUENCE_INDEX_PROPS and (prop.text or "").lstrip("-").isdigit():
            value = int(prop.text or 0)
            if value >= group_first:
                prop.text = str(value + 1)
        elif name == "kdenlive:sequenceproperties.groups" and prop.text and prop.text.strip():
            prop.text = _shift_groups(prop.text, group_first)
    project.changes.append(f"track indexes from {first} shifted up by one")


def _shift_groups(text: str, first: int) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProjectError("unsupported_structure", "Clip groups data is not valid JSON.") from exc

    def walk(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if node.get("type") == "Leaf" and isinstance(node.get("data"), str):
                parts = node["data"].split(":")
                if parts and parts[0].lstrip("-").isdigit() and int(parts[0]) >= first:
                    parts[0] = str(int(parts[0]) + 1)
                    node["data"] = ":".join(parts)
            for child in node.get("children", []) or []:
                walk(child)

    walk(data)
    return json.dumps(data, indent=4) + "\n"


def _new_track_elements(
    project: EditableProject, kind: str, name: str | None
) -> tuple[list[ET.Element], str]:
    root = project.root
    first_pl = _next_id(root, "playlist")
    second_pl = f"playlist{int(first_pl[len('playlist') :]) + 1}"
    tractor_id = _next_id(root, "tractor")
    hide = "video" if kind == "audio" else "audio"
    playlists = []
    for pid in (first_pl, second_pl):
        pl = ET.Element("playlist", {"id": pid})
        if kind == "audio":
            _prop(pl, "kdenlive:audio_track", "1")
        playlists.append(pl)
    tractor = ET.Element("tractor", {"id": tractor_id, "in": "00:00:00.000"})
    if kind == "audio":
        _prop(tractor, "kdenlive:audio_track", "1")
    if name:
        _prop(tractor, "kdenlive:track_name", name)
    for key, value in (
        ("kdenlive:trackheight", "62"),
        ("kdenlive:timeline_active", "1"),
        ("kdenlive:collapsed", "0"),
        ("kdenlive:thumbs_format", ""),
        ("kdenlive:audio_rec", ""),
    ):
        _prop(tractor, key, value)
    for pid in (first_pl, second_pl):
        ET.SubElement(tractor, "track", {"hide": hide, "producer": pid})
    if kind == "audio":
        next_filter = int(_next_id(root, "filter")[len("filter") :])
        for offset, (service, extra) in enumerate(
            (
                ("volume", {"window": "75", "max_gain": "20dB", "channel_mask": "-1"}),
                ("panner", {"channel": "-1"}),
                (
                    "audiolevel",
                    {"iec_scale": "0", "prefix": "meta.media.audio_level.", "dbpeak": "1"},
                ),
            )
        ):
            flt = ET.SubElement(tractor, "filter", {"id": f"filter{next_filter + offset}"})
            for key, value in extra.items():
                _prop(flt, key, value)
            _prop(flt, "mlt_service", service)
            _prop(flt, "internal_added", "237")
            if service == "panner":
                _prop(flt, "start", "0.5")
            _prop(flt, "disable", "1")
    for el in (*playlists, tractor):
        el.tail = "\n "
    return [*playlists, tractor], tractor_id


def _internal_transition(project: EditableProject, kind: str, b_track: int) -> ET.Element:
    transition = ET.Element("transition", {"id": _next_id(project.root, "transition")})
    _prop(transition, "a_track", "0")
    _prop(transition, "b_track", str(b_track))
    if kind == "audio":
        for key, value in (
            ("mlt_service", "mix"),
            ("kdenlive_id", "mix"),
            ("internal_added", "237"),
            ("always_active", "1"),
            ("accepts_blanks", "1"),
            ("sum", "1"),
        ):
            _prop(transition, key, value)
    else:
        for key, value in (
            ("compositing", "0"),
            ("mlt_service", "qtblend"),
            ("kdenlive_id", "qtblend"),
            ("internal_added", "237"),
            ("always_active", "1"),
        ):
            _prop(transition, key, value)
    transition.tail = "\n  "
    return transition


def add_track(
    project: EditableProject, kind: str, name: str | None = None, sequence: str | None = None
) -> TrackAdded:
    if kind not in ("audio", "video"):
        raise ProjectError("invalid_argument", "kind must be 'audio' or 'video'.")
    if name is not None and (len(name) > 200 or any(ord(c) < 32 for c in name)):
        raise ProjectError(
            "invalid_argument", "Track names must be at most 200 printable characters."
        )
    seq = _sequence(project, sequence)
    tracks = _track_targets(seq)
    if len(tracks) - 1 >= MAX_TRACKS:
        raise ProjectError("too_many_tracks", f"Sequences are limited to {MAX_TRACKS} tracks here.")
    elements, tractor_id = _new_track_elements(project, kind, name)
    anchor = list(project.root).index(seq)
    for offset, el in enumerate(elements):
        project.root.insert(anchor + offset, el)
        project.doc.by_id[el.get("id", "")] = el
    kinds = []
    for t in tracks[1:]:
        target = project.doc.by_id.get(t.get("producer") or "")
        kinds.append(
            "audio"
            if target is not None and props(target).get("kdenlive:audio_track") == "1"
            else "video"
        )
    if kind == "video":
        index = len(tracks)  # on top of everything
        new_ref = ET.Element("track", {"producer": tractor_id})
        new_ref.tail = tracks[-1].tail
        seq.insert(list(seq).index(tracks[-1]) + 1, new_ref)
        label = f"V{kinds.count('video') + 1}"
    else:
        index = 1  # bottom audio track, right above the background
        _shift_indexes(project, seq, 1)
        new_ref = ET.Element("track", {"producer": tractor_id})
        new_ref.tail = tracks[0].tail
        seq.insert(list(seq).index(tracks[0]) + 1, new_ref)
        label = f"A{kinds.count('audio') + 1}"
    transitions = seq.findall("transition")
    position = list(seq).index(transitions[0]) if transitions else len(seq)
    if kind == "video":
        position = list(seq).index(transitions[-1]) + 1 if transitions else len(seq)
    seq.insert(position, _internal_transition(project, kind, index))
    count = len(tracks)  # previous count + background - 1 + new one
    for prop_name in (
        "kdenlive:sequenceproperties.tracksCount",
        "kdenlive:sequenceproperties.tracks",
    ):
        _set_prop(seq, prop_name, str(count))
    project.changes.append(f"track: added {kind} track {label}" + (f" '{name}'" if name else ""))
    return TrackAdded(label=label, kind=kind, name=name, index=index)
