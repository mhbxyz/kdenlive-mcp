"""Relink missing (or moved) media in a project copy (#17), keeping every timeline edit.

Kdenlive 26.08 stores a media path in every copy of a clip: the bin clip and one ``<chain>`` per
timeline instance, each with ``resource``, ``kdenlive:file_hash`` and ``kdenlive:file_size``.
Relinking rewrites those three properties on every copy and nothing else: clip ids, timing,
effects and the other media are untouched.

Before writing, MLT reads the replacement under the project's profile (like add_media_to_bin)
and the result is compared with what the project uses:

- **refused** (``[media_mismatch]``): fewer frames than the timeline uses, or a missing video or
  audio stream that timeline clips use;
- **warnings**: a longer file, another frame size or rate, extra streams, or different bytes
  (``kdenlive:file_hash`` differs: it is another file, not the same one moved).

Clips using a proxy, image sequences and generated clips are refused.
"""

from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from mcp_kdenlive import create, media
from mcp_kdenlive.edits import _profile_file, _set_prop
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import props
from mcp_kdenlive.media import URL_RE, _Resolver
from mcp_kdenlive.writer import EditableProject

MAX_MAPPINGS = 100
META = (
    "meta.media.width",
    "meta.media.height",
    "meta.media.frame_rate_num",
    "meta.media.frame_rate_den",
)


class Relinked(BaseModel):
    bin_id: str
    old_reference: str | None
    new_reference: str
    new_path: str
    instances: int = Field(description="Copies rewritten: the bin clip and its timeline instances.")
    warnings: list[str]


class RelinkResult(BaseModel):
    relinked: list[Relinked]


@dataclass
class _Clip:
    bin_id: str
    copies: list[ET.Element]
    reference: str | None
    resolved: str | None
    original: str | None = None
    used_frames: int = 0  # highest source frame used on any timeline, + 1
    on_video: bool = False
    on_audio: bool = False
    warnings: list[str] = field(default_factory=list)


def _clips(edit: EditableProject) -> dict[str, _Clip]:
    doc = edit.doc
    resolver = _Resolver(doc)
    in_bin = {e.get("producer") for e in doc.main_bin.findall("entry")}
    found: dict[str, _Clip] = {}
    for el in doc.root:
        if el.tag not in ("chain", "producer", "tractor"):
            continue
        p = props(el)
        bin_id = p.get("kdenlive:id")
        if not bin_id:
            continue
        clip = found.get(bin_id)
        if clip is None:
            clip = found[bin_id] = _Clip(bin_id, [], None, None)
        clip.copies.append(el)
        if el.get("id") in in_bin:
            clip.reference = p.get("resource")
            clip.original = p.get("kdenlive:originalurl")
            if clip.reference and el.tag != "tractor":
                clip.resolved = str(Path(resolver.resolve(clip.reference)[0]).resolve())
    # What the timelines use, from every playlist entry (no truncated view).
    for playlist in doc.root.iter("playlist"):
        if playlist is doc.main_bin:
            continue
        for entry in playlist.findall("entry"):
            producer = doc.by_id.get(entry.get("producer") or "")
            if producer is None:
                continue
            p = props(producer)
            used = found.get(p.get("kdenlive:id", ""))
            if used is None:
                continue
            length = p.get("length")
            default_out = doc.time(length, what="length") - 1 if length else 0
            out = doc.time(entry.get("out"), default=default_out, what="entry out")
            used.used_frames = max(used.used_frames, out + 1)
            has_video = int(p.get("video_index", "-1") or -1) >= 0
            has_audio = int(p.get("audio_index", "-1") or -1) >= 0
            used.on_video |= has_video and p.get("set.test_image") != "1"
            used.on_audio |= has_audio and p.get("set.test_audio") != "1"
    return found


def _select(clips: dict[str, _Clip], key: str) -> _Clip:
    if key in clips:
        return clips[key]
    normalized = str(Path(key).resolve()) if Path(key).is_absolute() else None
    matches = [
        c
        for c in clips.values()
        if key in (c.reference, c.resolved, c.original)
        or (normalized is not None and c.resolved == normalized)
    ]
    if not matches:
        raise ProjectError(
            "clip_not_found",
            f"No bin clip with id or media reference {key!r}.",
            "Use the bin_id or the reference/resolved_path from list_project_media.",
        )
    if len(matches) > 1:
        raise ProjectError(
            "invalid_argument",
            f"{key!r} is used by bin clips {sorted(c.bin_id for c in matches)}.",
            "Relink each of them by bin_id.",
        )
    return matches[0]


def _check_clip(clip: _Clip) -> ET.Element:
    bin_copy = next((el for el in clip.copies if props(el).get("resource") == clip.reference), None)
    p = props(bin_copy) if bin_copy is not None else {}
    if bin_copy is None or bin_copy.tag == "tractor" or not clip.reference:
        raise ProjectError("invalid_argument", f"Bin clip {clip.bin_id} is not a media file.")
    if any(props(el).get("mlt_service") == "timewarp" for el in clip.copies):
        raise ProjectError(
            "unsupported_structure",
            f"Bin clip {clip.bin_id} has speed-changed instances (timewarp); relinking them is "
            "not supported.",
            "Relink it in Kdenlive (Missing clips dialog), or reset the speed first.",
        )
    proxy = p.get("kdenlive:proxy")
    if proxy and proxy != "-":
        raise ProjectError(
            "unsupported_structure",
            f"Bin clip {clip.bin_id} uses a proxy; relinking it is not supported.",
            "Disable the proxy for this clip in Kdenlive, then relink.",
        )
    service = p.get("mlt_service", "")
    if not service.startswith("avformat") or URL_RE.match(clip.reference):
        raise ProjectError(
            "invalid_argument",
            f"Bin clip {clip.bin_id} ({service or 'unknown'}) is not an audio/video file.",
        )
    return bin_copy


def _target(raw: str) -> Path:
    if not raw or URL_RE.match(raw) or not Path(raw).is_absolute():
        raise ProjectError(
            "invalid_path", f"The new media path must be an absolute local path: {raw!r}."
        )
    checked = media.inspect_file(raw)
    if checked.status is not media.MediaStatus.OK:
        raise ProjectError(
            "media_unreadable",
            f"{raw}: {checked.status.value}" + (f" ({checked.detail})" if checked.detail else ""),
        )
    return Path(checked.path)


def _compare(clip: _Clip, old: dict[str, str], new: dict[str, str], path: Path) -> None:
    length = int(new.get("length", "0") or 0)
    has_video = int(new.get("video_index", "-1") or -1) >= 0
    has_audio = int(new.get("audio_index", "-1") or -1) >= 0
    if length < clip.used_frames:
        raise ProjectError(
            "media_mismatch",
            f"{path.name} has {length} frames at the project rate, but the timeline uses "
            f"frames up to {clip.used_frames - 1} of bin clip {clip.bin_id}.",
        )
    if (clip.on_video and not has_video) or (clip.on_audio and not has_audio):
        missing = "video" if clip.on_video and not has_video else "audio"
        raise ProjectError(
            "media_mismatch",
            f"{path.name} has no {missing}, which the timeline uses for bin clip {clip.bin_id}.",
        )
    old_length = int(old.get("length", "0") or 0)
    if old_length and length > old_length:
        clip.warnings.append(
            f"{path.name} is longer than the original ({length} > {old_length} frames)."
        )
    elif old_length and length < old_length:
        clip.warnings.append(
            f"{path.name} is shorter than the original ({length} < {old_length} frames); the "
            "timeline still fits."
        )
    differs = [
        k.removeprefix("meta.media.")
        for k in META
        if old.get(k) and new.get(k) and old[k] != new[k]
    ]
    if differs:
        clip.warnings.append(f"{path.name} differs from the original in {', '.join(differs)}.")
    old_streams = (
        int(old.get("video_index", "-1") or -1) >= 0,
        int(old.get("audio_index", "-1") or -1) >= 0,
    )
    if (has_video, has_audio) != old_streams:
        clip.warnings.append(
            f"{path.name} does not have the same audio/video streams as the original."
        )


def relink_media(edit: EditableProject, mapping: dict[str, str]) -> RelinkResult:
    """Point bin clips (by bin_id, stored reference or resolved path) at new files."""
    if not mapping:
        raise ProjectError("invalid_argument", "Pass at least one 'old → new' mapping.")
    if len(mapping) > MAX_MAPPINGS:
        raise ProjectError("invalid_argument", f"At most {MAX_MAPPINGS} mappings per call.")
    clips = _clips(edit)
    plan: list[tuple[_Clip, ET.Element, Path]] = []
    seen: set[str] = set()
    for key, raw in mapping.items():
        clip = _select(clips, str(key))
        if clip.bin_id in seen:
            raise ProjectError("invalid_argument", f"Bin clip {clip.bin_id} is mapped twice.")
        seen.add(clip.bin_id)
        plan.append((clip, _check_clip(clip), _target(raw)))
    effective = Path(_Resolver(edit.doc).effective).resolve()
    results = []
    with tempfile.TemporaryDirectory(prefix="kdenlive-mcp-relink-") as tmp:
        profile_path = _profile_file(edit, Path(tmp))
        for clip, bin_copy, path in plan:
            new = props(create.melt_producer(path, str(profile_path)))
            old = props(bin_copy)
            _compare(clip, old, new, path)
            file_hash, size = create.kdenlive_file_hash(path)
            if old.get("kdenlive:file_hash") and old["kdenlive:file_hash"] != file_hash:
                clip.warnings.append(
                    f"{path.name} is a different file (its Kdenlive hash differs)."
                )
            reference = create._media_reference(path, effective)
            others = sorted(
                c.bin_id
                for c in clips.values()
                if c.bin_id != clip.bin_id and c.resolved == str(path.resolve())
            )
            if others:
                clip.warnings.append(
                    f"{path.name} is also used by bin clip {', '.join(others)}: the bin will have "
                    "two clips for one file."
                )
            indexes = {k: new[k] for k in ("video_index", "audio_index") if k in new}
            if any(old.get(k) != v for k, v in indexes.items()):
                clip.warnings.append(
                    f"{path.name} orders its streams differently; video_index/audio_index were "
                    "updated on every copy."
                )
            for el in clip.copies:
                _set_prop(el, "resource", reference)
                _set_prop(el, "kdenlive:file_hash", file_hash)
                _set_prop(el, "kdenlive:file_size", str(size))
                for name, value in indexes.items():
                    _set_prop(el, name, value)
            results.append(
                Relinked(
                    bin_id=clip.bin_id,
                    old_reference=clip.reference,
                    new_reference=reference,
                    new_path=str(path),
                    instances=len(clip.copies),
                    warnings=clip.warnings,
                )
            )
            edit.changes.append(f"relink: bin clip {clip.bin_id} {clip.reference} → {reference}")
    return RelinkResult(relinked=results)
