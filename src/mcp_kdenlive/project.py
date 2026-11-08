"""Read-only project views: summary and timeline (independent of MCP).

Time conventions used in every result:

* All positions and durations are **integer frames** at the project frame rate
  ``frame_rate.num / frame_rate.den``.
* Timeline ranges are half-open: ``start`` is the first frame, ``end`` is the first frame
  *after* the clip (``end = start + duration``).
* Source ranges follow MLT: ``source_in`` and ``source_out`` are both **inclusive** frame
  indexes in the source clip, so ``duration = source_out - source_in + 1``.
* ``*_timecode`` fields are non-drop ``HH:MM:SS:FF`` labels for humans, using the nominal
  integer rate (30 for 30000/1001); use the frame numbers for computation.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import PurePosixPath

from pydantic import BaseModel, Field

from mcp_kdenlive import clip_effects
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import KdenliveDocument, props
from mcp_kdenlive.timecode import FrameRate, frames_to_seconds, frames_to_timecode

FRESHNESS_NOTE = (
    "Read from the file on disk. If the project is open in Kdenlive, unsaved changes are not "
    "visible here, and Kdenlive may overwrite the file when it saves."
)
DEFAULT_MAX_CLIPS = 500
MAX_CLIPS_LIMIT = 10_000


# ---------------------------------------------------------------- models -------------
class FrameRateInfo(BaseModel):
    num: int
    den: int
    fps: float = Field(description="num/den as a float, for display only.")


class ProfileInfo(BaseModel):
    description: str | None
    kdenlive_profile: str | None = Field(description="Profile identifier stored by Kdenlive.")
    width: int | None
    height: int | None
    frame_rate: FrameRateInfo
    progressive: bool | None
    sample_aspect: str | None
    display_aspect: str | None
    colorspace: str | None


class FileInfoModel(BaseModel):
    path: str
    size_bytes: int
    sha256: str = Field(description="Content hash; use it to detect changes between calls.")
    modified: str = Field(description="File modification time (UTC, ISO 8601).")


class SequenceSummary(BaseModel):
    uuid: str
    name: str | None
    active: bool
    duration_frames: int
    duration_timecode: str
    duration_seconds: float
    audio_tracks: int
    video_tracks: int
    clip_count: int


class ProjectSummary(BaseModel):
    file: FileInfoModel
    kdenlive_version: str | None = Field(description="Kdenlive version that last saved the file.")
    document_version: str | None
    format_generation: int = Field(description="5 = Kdenlive ≥ 23.04 (multiple sequences).")
    mlt_version: str | None
    profile: ProfileInfo
    bin_clip_count: int
    sequences: list[SequenceSummary]
    warnings: list[str]
    notes: list[str]


class AudioSettings(BaseModel):
    """Audio effects of a clip instance that the server understands (#13)."""

    level_db: float | None = Field(description="Constant volume in dB; null when not set.")
    fade_in: int = Field(description="Audio fade-in length in frames (0 = none).")
    fade_out: int = Field(description="Audio fade-out length in frames (0 = none).")


class ClipInfo(BaseModel):
    ref: str = Field(
        description="Clip reference '<track>@<start>' (e.g. 'V1@90'; '#1' is appended for the "
        "secondary layer when two clips start on the same frame). Valid for this file revision; "
        "a clip keeps its ref as long as it does not move."
    )
    layer: int = Field(description="0 = main playlist, 1 = secondary playlist used for mixes.")
    start: int
    end: int = Field(description="Exclusive end frame (start + duration).")
    duration: int
    start_timecode: str
    source_in: int = Field(description="First source frame (inclusive).")
    source_out: int = Field(description="Last source frame (inclusive).")
    producer_id: str | None = Field(description="MLT id of the timeline producer.")
    bin_id: str | None = Field(description="Kdenlive bin clip id (kdenlive:id).")
    name: str | None
    resource: str | None = Field(description="Media reference as stored in the project.")
    service: str | None = Field(
        description="MLT service, e.g. avformat-novalidate, color, tractor."
    )
    effects: list[str] = Field(description="Effect identifiers applied to this clip instance.")
    group: list[str] = Field(
        default_factory=list,
        description="Refs of the clips grouped with this one (Kdenlive audio/video pair); "
        "edits apply to the whole group.",
    )
    audio: AudioSettings | None = Field(
        default=None,
        description="Level and fades set on this clip (volume, fadein, fadeout effects); "
        "null when none.",
    )


class MixInfo(BaseModel):
    """Same-track transition between the two playlists of a track (Kdenlive 'mix')."""

    service: str | None
    kdenlive_id: str | None
    start: int | None
    end: int | None = Field(
        description="Exclusive end frame: where the left clip ends (Kdenlive stores it as `out`)."
    )
    cut_offset: int | None = Field(
        description="kdenlive:mixcut: frames between the original cut and the end of the mix."
    )
    cut: int | None = Field(
        default=None,
        description="Timeline frame of the original cut (end - cut_offset): where the clips "
        "would meet without the mix.",
    )


class TrackInfo(BaseModel):
    index: int = Field(description="MLT track index inside the sequence (0 is the background).")
    label: str = Field(
        description="Kdenlive-style label: V1 is the lowest video track, A1 the "
        "audio track nearest to video (derived)."
    )
    kind: str = Field(description="'audio' or 'video'.")
    name: str | None
    locked: bool
    disabled: str | None = Field(
        description="Raw MLT 'hide' attribute on the sequence track: audio, video or both."
    )
    effects: list[str]
    mixes: list[MixInfo]
    clip_count: int
    clips: list[ClipInfo]


class CompositionInfo(BaseModel):
    service: str | None
    kdenlive_id: str | None
    a_track: int | None
    b_track: int | None
    start: int | None
    end: int | None = Field(description="Exclusive end frame.")


class GuideInfo(BaseModel):
    position: int
    timecode: str
    comment: str | None
    category: int | None


class Timeline(BaseModel):
    file: FileInfoModel
    sequence_uuid: str
    sequence_name: str | None
    frame_rate: FrameRateInfo
    duration_frames: int
    duration_timecode: str
    tracks: list[TrackInfo]
    compositions: list[CompositionInfo]
    guides: list[GuideInfo]
    truncated: bool = Field(description="True if clips were omitted because of max_clips.")
    returned_clips: int
    total_clips: int
    conventions: str
    warnings: list[str]
    notes: list[str]


# ---------------------------------------------------------------- helpers ------------
def _rate_info(rate: FrameRate) -> FrameRateInfo:
    return FrameRateInfo(num=rate.num, den=rate.den, fps=round(rate.num / rate.den, 6))


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _effect_names(element: ET.Element) -> list[str]:
    names = []
    for flt in element.findall("filter"):
        p = props(flt)
        if p.get("internal_added"):
            continue
        names.append(p.get("kdenlive_id") or p.get("mlt_service") or "unknown")
    return names


def _audio_settings(entry: ET.Element, doc: KdenliveDocument) -> AudioSettings | None:
    level = clip_effects.level_db(entry)
    fade_in = clip_effects.fade_length(entry, "fadein", doc.time)
    fade_out = clip_effects.fade_length(entry, "fadeout", doc.time)
    if level is None and not fade_in and not fade_out:
        return None
    return AudioSettings(level_db=level, fade_in=fade_in, fade_out=fade_out)


def _clip_name(producer: ET.Element | None) -> str | None:
    if producer is None:
        return None
    p = props(producer)
    if p.get("kdenlive:clipname"):
        return p["kdenlive:clipname"]
    resource = p.get("resource")
    if resource and p.get("mlt_service", "").startswith(("avformat", "qimage", "pixbuf")):
        return PurePosixPath(resource).name
    return resource or None


def _file_model(doc: KdenliveDocument) -> FileInfoModel:
    i = doc.info
    return FileInfoModel(path=i.path, size_bytes=i.size, sha256=i.sha256, modified=i.modified)


class _SequenceView:
    """Walks one sequence tractor into tracks and clips."""

    def __init__(self, doc: KdenliveDocument, tractor: ET.Element) -> None:
        self.doc = doc
        self.tractor = tractor
        p = props(tractor)
        self.uuid: str = p.get("kdenlive:uuid") or tractor.get("id") or ""
        self.name = p.get("kdenlive:clipname")
        self.tracks: list[TrackInfo] = []
        self.compositions: list[CompositionInfo] = []
        self._build()

    def _playlist_clips(self, playlist: ET.Element, track_index: int, layer: int) -> list[ClipInfo]:
        doc = self.doc
        clips: list[ClipInfo] = []
        position = 0
        for entry_index, item in enumerate(el for el in playlist if el.tag in ("entry", "blank")):
            if item.tag == "blank":
                position += doc.time(item.get("length"), what="blank length")
                continue
            producer_id = item.get("producer")
            producer = doc.by_id.get(producer_id or "")
            pp = props(producer) if producer is not None else {}
            default_out = None
            if producer is not None:
                default_out = doc.time(producer.get("out"), default=-1, what="producer out")
                if default_out < 0 and pp.get("length"):
                    default_out = doc.time(pp["length"], what="length") - 1
            source_in = doc.time(item.get("in"), default=0, what="entry in")
            source_out = doc.time(item.get("out"), default=default_out, what="entry out")
            if source_out < source_in:
                raise ProjectError(
                    "unsupported_structure",
                    f"entry {producer_id!r} on track {track_index} ends before it starts",
                )
            duration = source_out - source_in + 1
            entry_props = props(item)
            clips.append(
                ClipInfo(
                    ref=f"{track_index}:{layer}:{entry_index}",
                    layer=layer,
                    start=position,
                    end=position + duration,
                    duration=duration,
                    start_timecode=frames_to_timecode(position, doc.rate),
                    source_in=source_in,
                    source_out=source_out,
                    producer_id=producer_id,
                    bin_id=entry_props.get("kdenlive:id") or pp.get("kdenlive:id"),
                    name=_clip_name(producer),
                    resource=pp.get("resource")
                    if producer is not None and producer.tag != "tractor"
                    else None,
                    service=pp.get("mlt_service")
                    or (producer.tag if producer is not None else None),
                    effects=_effect_names(item),
                    audio=_audio_settings(item, doc),
                )
            )
            position += duration
        return clips

    def _build(self) -> None:
        doc = self.doc
        raw_tracks = list(self.tractor.findall("track"))
        kinds: list[tuple[int, ET.Element, ET.Element, str]] = []
        for index, track_ref in enumerate(raw_tracks):
            target = doc.by_id.get(track_ref.get("producer") or "")
            if target is None:
                doc.warnings.append(f"Track {index} references a missing producer.")
                continue
            if index == 0 and (
                target.tag not in {"tractor", "playlist"} or target.get("id") == "black_track"
            ):
                continue  # black background producer
            tp = props(target)
            kind = "audio" if tp.get("kdenlive:audio_track") == "1" else "video"
            kinds.append((index, track_ref, target, kind))
        audio = [k for k in kinds if k[3] == "audio"]
        video = [k for k in kinds if k[3] == "video"]
        labels = {k[0]: f"A{len(audio) - i}" for i, k in enumerate(audio)}
        labels.update({k[0]: f"V{i + 1}" for i, k in enumerate(video)})
        for index, track_ref, target, kind in kinds:
            tp = props(target)
            if target.tag == "tractor":
                layers = [doc.by_id.get(t.get("producer") or "") for t in target.findall("track")]
            else:  # generation < 4: the track is a single playlist
                layers = [target]
            clips: list[ClipInfo] = []
            for layer, playlist in enumerate(layers):
                if playlist is None or playlist.tag != "playlist":
                    doc.warnings.append(f"Track {index} layer {layer} is not a playlist; skipped.")
                    continue
                clips.extend(self._playlist_clips(playlist, index, layer))
            clips.sort(key=lambda c: (c.start, c.layer))
            label = labels[index]
            starts = Counter(c.start for c in clips)
            for c in clips:
                c.ref = f"{label}@{c.start}" + (f"#{c.layer}" if starts[c.start] > 1 else "")
            mixes = (
                sorted(
                    (
                        self._mix(t)
                        for t in target.findall("transition")
                        if not props(t).get("internal_added")
                    ),
                    key=lambda m: m.start or 0,
                )
                if target.tag == "tractor"
                else []
            )
            self.tracks.append(
                TrackInfo(
                    index=index,
                    label=labels[index],
                    kind=kind,
                    name=tp.get("kdenlive:track_name") or None,
                    locked=tp.get("kdenlive:locked_track") == "1",
                    disabled=track_ref.get("hide"),
                    effects=_effect_names(target),
                    mixes=mixes,
                    clip_count=len(clips),
                    clips=clips,
                )
            )
        self._link_groups()
        for transition in self.tractor.findall("transition"):
            p = props(transition)
            if p.get("internal_added"):
                continue
            start, end = self._range(transition, "composition")
            self.compositions.append(
                CompositionInfo(
                    service=p.get("mlt_service"),
                    kdenlive_id=p.get("kdenlive_id"),
                    a_track=_int_or_none(p.get("a_track")),
                    b_track=_int_or_none(p.get("b_track")),
                    start=start,
                    end=end,
                )
            )

    def _link_groups(self) -> None:
        """Fill ClipInfo.group from Kdenlive's AVSplit groups, at any depth."""
        raw = props(self.tractor).get("kdenlive:sequenceproperties.groups", "").strip()
        try:
            groups = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            return
        by_key: dict[tuple[int, int], list[ClipInfo]] = {}
        for t in self.tracks:
            for c in t.clips:
                by_key.setdefault((t.index - 1, c.start), []).append(c)

        def walk(node: object) -> None:
            if not isinstance(node, dict):
                return
            children = node.get("children", [])
            if node.get("type") == "AVSplit":
                members = []
                for leaf in children:
                    data = leaf.get("data") if isinstance(leaf, dict) else None
                    try:
                        track, pos = (int(x) for x in str(data).split(":")[:2])
                    except ValueError:
                        continue
                    found = by_key.get((track, pos), [])
                    if len(found) == 1:  # ambiguous positions are not linked
                        members.append(found[0])
                for clip in members:
                    clip.group = [m.ref for m in members if m is not clip]
            for child in children:
                walk(child)

        for group in groups if isinstance(groups, list) else []:
            walk(group)

    def _range(self, element: ET.Element, what: str) -> tuple[int | None, int | None]:
        start = self.doc.time(element.get("in"), default=-1, what=f"{what} in")
        out = self.doc.time(element.get("out"), default=-1, what=f"{what} out")
        return (start if start >= 0 else None, out + 1 if out >= 0 else None)

    def _mix(self, transition: ET.Element) -> MixInfo:
        p = props(transition)
        start, _ = self._range(transition, "mix")
        # Kdenlive writes a mix's `out` as the frame where the left clip ends (exclusive),
        # unlike other MLT ranges: verified on mixes made by Kdenlive 26.08.2 (#61).
        out = self.doc.time(transition.get("out"), default=-1, what="mix out")
        end = out if out >= 0 else None
        offset = _int_or_none(p.get("kdenlive:mixcut"))
        return MixInfo(
            service=p.get("mlt_service"),
            kdenlive_id=p.get("kdenlive_id"),
            start=start,
            end=end,
            cut_offset=offset,
            cut=end - offset if end is not None and offset is not None else None,
        )

    @property
    def duration(self) -> int:
        return max((c.end for t in self.tracks for c in t.clips), default=0)

    def guides(self) -> list[GuideInfo]:
        raw = props(self.tractor).get("kdenlive:sequenceproperties.guides")
        if raw is None:
            raw = self.doc.doc_property("guides")
        if not raw or not raw.strip():
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self.doc.warnings.append("Guides data is not valid JSON; guides omitted.")
            return []
        guides = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or "pos" not in item:
                continue
            try:
                pos = int(item["pos"])
            except (TypeError, ValueError):
                continue
            category = item.get("type")
            guides.append(
                GuideInfo(
                    position=pos,
                    timecode=frames_to_timecode(pos, self.doc.rate),
                    comment=item.get("comment"),
                    category=category if isinstance(category, int) else None,
                )
            )
        return sorted(guides, key=lambda g: g.position)


def _sequences(doc: KdenliveDocument) -> list[_SequenceView]:
    tractors = doc.sequence_tractors()
    if not tractors:
        raise ProjectError("unsupported_structure", "The project contains no timeline tractor.")
    return [_SequenceView(doc, t) for t in tractors]


# ---------------------------------------------------------------- public API ---------
def used_bin_ids(doc: KdenliveDocument) -> set[str]:
    """Bin clip ids placed on any timeline sequence."""
    return {c.bin_id for s in _sequences(doc) for t in s.tracks for c in t.clips if c.bin_id}


def summarize(doc: KdenliveDocument) -> ProjectSummary:
    profile = doc.root.find("profile")
    assert profile is not None  # noqa: S101 - validated by kdenlive_doc.load
    sequences = _sequences(doc)
    active = doc.active_sequence_uuid()
    if active is None or all(s.uuid != active for s in sequences):
        active = sequences[-1].uuid if doc.generation < 5 else sequences[0].uuid
    bin_entries = [e for e in doc.main_bin.findall("entry")]
    seq_ids = {s.tractor.get("id") for s in sequences}
    bin_clips = [e for e in bin_entries if e.get("producer") not in seq_ids]
    progressive = profile.get("progressive")
    return ProjectSummary(
        file=_file_model(doc),
        kdenlive_version=doc.doc_property("kdenliveversion"),
        document_version=doc.document_version,
        format_generation=doc.generation,
        mlt_version=doc.root.get("version"),
        profile=ProfileInfo(
            description=profile.get("description"),
            kdenlive_profile=doc.doc_property("profile"),
            width=_int_or_none(profile.get("width")),
            height=_int_or_none(profile.get("height")),
            frame_rate=_rate_info(doc.rate),
            progressive=None if progressive is None else progressive == "1",
            sample_aspect=f"{profile.get('sample_aspect_num')}/{profile.get('sample_aspect_den')}",
            display_aspect=f"{profile.get('display_aspect_num')}/{profile.get('display_aspect_den')}",
            colorspace=profile.get("colorspace"),
        ),
        bin_clip_count=len(bin_clips),
        sequences=[
            SequenceSummary(
                uuid=s.uuid,
                name=s.name,
                active=s.uuid == active,
                duration_frames=s.duration,
                duration_timecode=frames_to_timecode(s.duration, doc.rate),
                duration_seconds=round(frames_to_seconds(s.duration, doc.rate), 3),
                audio_tracks=sum(t.kind == "audio" for t in s.tracks),
                video_tracks=sum(t.kind == "video" for t in s.tracks),
                clip_count=sum(t.clip_count for t in s.tracks),
            )
            for s in sequences
        ],
        warnings=list(dict.fromkeys(doc.warnings)),
        notes=[FRESHNESS_NOTE],
    )


def select_sequence(doc: KdenliveDocument, sequence: str | None = None) -> _SequenceView:
    """The sequence named by uuid, name or id; by default the active one."""
    sequences = _sequences(doc)
    if sequence:
        matches = [s for s in sequences if sequence in (s.uuid, s.name, s.tractor.get("id"))]
        if not matches:
            raise ProjectError(
                "sequence_not_found",
                f"No sequence matches {sequence!r}.",
                f"Available: {[s.uuid for s in sequences]}",
            )
        if len(matches) > 1:
            raise ProjectError(
                "ambiguous_sequence",
                f"{sequence!r} matches several sequences.",
                "Pass the sequence uuid.",
            )
        view = matches[0]
    else:
        active = doc.active_sequence_uuid()
        view = next((s for s in sequences if s.uuid == active), None) or (
            sequences[-1] if doc.generation < 5 else sequences[0]
        )
    return view


def timeline(
    doc: KdenliveDocument, sequence: str | None = None, max_clips: int = DEFAULT_MAX_CLIPS
) -> Timeline:
    if not 1 <= max_clips <= MAX_CLIPS_LIMIT:
        raise ProjectError(
            "invalid_argument", f"max_clips must be between 1 and {MAX_CLIPS_LIMIT}."
        )
    view = select_sequence(doc, sequence)
    total = sum(t.clip_count for t in view.tracks)
    remaining = max_clips
    tracks: list[TrackInfo] = []
    for track in view.tracks:
        kept = track.clips[: max(remaining, 0)]
        remaining -= len(kept)
        tracks.append(track.model_copy(update={"clips": kept}))
    returned = sum(len(t.clips) for t in tracks)
    duration = view.duration
    return Timeline(
        file=_file_model(doc),
        sequence_uuid=view.uuid,
        sequence_name=view.name,
        frame_rate=_rate_info(doc.rate),
        duration_frames=duration,
        duration_timecode=frames_to_timecode(duration, doc.rate),
        tracks=tracks,
        compositions=view.compositions,
        guides=view.guides(),
        truncated=returned < total,
        returned_clips=returned,
        total_clips=total,
        conventions=__doc__.split("Time conventions used in every result:", 1)[1].strip()
        if __doc__
        else "",
        warnings=list(dict.fromkeys(doc.warnings)),
        notes=[FRESHNESS_NOTE],
    )
