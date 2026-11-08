"""Create a new Kdenlive project with one media clip on the timeline.

The output is modeled on files written by Kdenlive 26.08.2 itself (see
tests/fixtures/PROVENANCE.md):

* the skeleton (tracks, internal mixes/compositing, bin folder, document properties) is an
  empty project saved by Kdenlive 26.08.2, shipped as ``templates/kdenlive-26.08-empty.kdenlive``;
* clip properties (length at the project frame rate, stream indexes, ``meta.media.*``) come
  from MLT itself (``melt -consumer xml``), exactly the data Kdenlive obtains from MLT;
* Kdenlive metadata is added like Kdenlive does: bin id, control uuid, file size and
  ``kdenlive:file_hash`` (MD5 of the file, or of its first and last MB above 2 MB);
* a clip with audio and video is placed on V1 and A1 and grouped (``AVSplit``), as Kdenlive's
  own insertion does; audio-only media goes to A1, video-only media to V1.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, Field

from mcp_kdenlive import kdenlive_doc, media, policy, presence, project, safexml
from mcp_kdenlive.environment import locate
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.process import ProcessTimeout, run
from mcp_kdenlive.timecode import FrameRate, frames_to_clock

TEMPLATE = "kdenlive-26.08-empty.kdenlive"
TEMPLATE_UUID = "{59f5860c-0555-4b4d-bae3-d86f87debb4e}"
TEMPLATE_KDENLIVE_VERSION = "26.08.2"
PROFILE_KEYS = (
    "description",
    "frame_rate_num",
    "frame_rate_den",
    "width",
    "height",
    "progressive",
    "sample_aspect_num",
    "sample_aspect_den",
    "display_aspect_num",
    "display_aspect_den",
    "colorspace",
)
# Track tractors of the template: tractor0/1 are audio (A2, A1), tractor2/3 video (V1, V2).
AUDIO_TRACK = ("tractor1", "playlist2", 1)  # A1: (tractor id, main playlist, group track index)
VIDEO_TRACK = ("tractor2", "playlist4", 2)  # V1
MELT_TIMEOUT = 60.0


# ---------------------------------------------------------------- profiles -----------
class ProfileInfo(BaseModel):
    name: str
    description: str | None
    width: int
    height: int
    frame_rate_num: int
    frame_rate_den: int
    progressive: bool


def profiles_dir(env: Mapping[str, str] | None = None) -> Path | None:
    environ = os.environ if env is None else env
    candidates = []
    if environ.get("MLT_PROFILES_PATH"):
        candidates.append(Path(environ["MLT_PROFILES_PATH"]))
    melt = locate("melt", environ)
    if melt:
        prefix = Path(melt).resolve().parent.parent
        candidates += [
            prefix / "share" / "mlt-7" / "profiles",
            prefix / "share" / "mlt" / "profiles",
        ]
    candidates += [Path("/usr/share/mlt-7/profiles"), Path("/usr/share/mlt/profiles")]
    return next((c for c in candidates if c.is_dir()), None)


def read_profile(name: str, env: Mapping[str, str] | None = None) -> dict[str, str]:
    directory = profiles_dir(env)
    if directory is None:
        raise ProjectError(
            "dependency_missing",
            "No MLT profile directory was found.",
            "Install MLT, or set MLT_PROFILES_PATH.",
        )
    if not name or "/" in name or name.startswith("."):
        raise ProjectError("unknown_profile", f"Invalid profile name {name!r}.")
    path = directory / name
    if not path.is_file():
        close = sorted(p.name for p in directory.iterdir() if name.lower()[:6] in p.name.lower())[
            :10
        ]
        raise ProjectError(
            "unknown_profile",
            f"No MLT profile named {name!r} in {directory}.",
            f"Similar: {close}" if close else "Call list_profiles for valid names.",
        )
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    try:
        FrameRate(int(values["frame_rate_num"]), int(values["frame_rate_den"]))
        int(values["width"]), int(values["height"])
    except (KeyError, ValueError) as exc:
        raise ProjectError(
            "unknown_profile", f"MLT profile {name!r} is incomplete ({exc})."
        ) from exc
    return values


def list_profiles(env: Mapping[str, str] | None = None) -> list[ProfileInfo]:
    directory = profiles_dir(env)
    found = []
    for path in sorted(directory.iterdir()) if directory else []:
        try:
            v = read_profile(path.name, env)
            found.append(
                ProfileInfo(
                    name=path.name,
                    description=v.get("description"),
                    width=int(v["width"]),
                    height=int(v["height"]),
                    frame_rate_num=int(v["frame_rate_num"]),
                    frame_rate_den=int(v["frame_rate_den"]),
                    progressive=v.get("progressive") == "1",
                )
            )
        except ProjectError:
            continue
    return found


def matching_profile(info: media.ProbeInfo, env: Mapping[str, str] | None = None) -> str:
    """The standard progressive MLT profile matching the media's size and frame rate."""
    video = next((s for s in info.streams if s.type == "video"), None)
    if video is None or not video.width or not video.height or not video.frame_rate:
        raise ProjectError(
            "profile_required",
            "The media has no usable video stream to match.",
            "Pass an explicit profile name (see list_profiles).",
        )
    num, _, den = video.frame_rate.partition("/")
    for p in list_profiles(env):
        same_rate = p.frame_rate_num * int(den or 1) == int(num) * p.frame_rate_den
        if (p.width, p.height) == (video.width, video.height) and same_rate and p.progressive:
            return p.name
    raise ProjectError(
        "profile_required",
        f"No standard MLT profile matches {video.width}x{video.height} at {video.frame_rate} fps.",
        "Pass an explicit profile name (see list_profiles).",
    )


# ---------------------------------------------------------------- media --------------
def kdenlive_file_hash(path: Path) -> tuple[str, int]:
    """Kdenlive's kdenlive:file_hash and kdenlive:file_size (ProjectClip::calculateHash)."""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > 2_000_000:
            data = fh.read(1_000_000)
            fh.seek(size - 1_000_000)
            data += fh.read()
        else:
            data = fh.read()
    return hashlib.md5(data, usedforsecurity=False).hexdigest(), size


def melt_producer(path: Path, profile: str, env: Mapping[str, str] | None = None) -> ET.Element:
    """Let MLT describe the media under `profile` (length, streams, meta.media.*)."""
    melt = locate("melt", env)
    if melt is None:
        raise ProjectError(
            "dependency_missing",
            "melt (MLT) is not available.",
            "Run diagnose_environment, or set KDENLIVE_MCP_MELT.",
        )
    with tempfile.TemporaryDirectory(prefix="kdenlive-mcp-melt-") as tmp:
        out = Path(tmp) / "probe.mlt"
        # "avformat:<path>" stops melt from parsing '=' or a leading '-' in file names.
        args = [melt, "-quiet", "-profile", profile, f"avformat:{path}", "-consumer", f"xml:{out}"]
        try:
            done = run(args, timeout=MELT_TIMEOUT, env=policy.child_env(env))
        except ProcessTimeout as exc:
            raise ProjectError(
                "media_unreadable", f"melt did not finish within {MELT_TIMEOUT:g}s."
            ) from exc
        if done.returncode != 0 or not out.is_file():
            tail = (done.stderr.strip().splitlines() or ["no output"])[-1][:300]
            raise ProjectError("media_unreadable", f"MLT cannot open {path}: {tail}")
        root, _ = safexml.parse_file(out)
    producer = root.find("producer")
    if producer is None:
        raise ProjectError("media_unreadable", f"MLT produced no clip for {path}.")
    return producer


# ---------------------------------------------------------------- result -------------
class CreateResult(BaseModel):
    project: str
    project_sha256: str
    profile: str
    frame_rate: str
    media: str
    media_reference: str = Field(description="Path stored in the project (relative when possible).")
    clip_duration_frames: int
    tracks: list[str] = Field(description="Tracks that received the clip, e.g. ['V1', 'A1'].")
    kdenlive_format: str
    warnings: list[str]
    notes: list[str]


# ---------------------------------------------------------------- building -----------
def _prop(parent: ET.Element, name: str, value: str) -> None:
    el = ET.SubElement(parent, "property", {"name": name})
    el.text = value


def _set_prop(parent: ET.Element, name: str, value: str) -> None:
    for p in parent.findall("property"):
        if p.get("name") == name:
            p.text = value
            return
    _prop(parent, name, value)


def _chain(
    chain_id: str, source: ET.Element, out_clock: str, overrides: dict[str, str]
) -> ET.Element:
    chain = ET.Element("chain", {"id": chain_id, "out": out_clock})
    seen: set[str] = set()
    for p in source.findall("property"):
        name = p.get("name") or ""
        if name in overrides or name in seen:
            continue
        seen.add(name)
        _prop(chain, name, p.text or "")
    for name, value in overrides.items():
        _prop(chain, name, value)
    return chain


def _load_template() -> ET.Element:
    text = (
        resources.files("mcp_kdenlive").joinpath("templates", TEMPLATE).read_text(encoding="utf-8")
    )
    new_uuid = "{" + str(uuid.uuid4()) + "}"
    return safexml.parse_bytes(text.replace(TEMPLATE_UUID, new_uuid).encode("utf-8"))


def _media_reference(media_path: Path, project_dir: Path) -> str:
    try:
        return str(media_path.relative_to(project_dir))
    except ValueError:
        return str(media_path)


def _apply_profile(
    root: ET.Element, main_bin: ET.Element, profile: dict[str, str], name: str, output: Path
) -> None:
    prof_el = root.find("profile")
    if prof_el is None:  # pragma: no cover - template invariant
        raise ProjectError("internal_error", "template has no profile")
    for key in sorted(PROFILE_KEYS):
        if key in profile:
            prof_el.set(key, profile[key])
    _set_prop(main_bin, "kdenlive:docproperties.profile", name)
    _set_prop(main_bin, "kdenlive:docproperties.documentid", str(int(time.time() * 1000)))
    _set_prop(main_bin, "kdenlive:docproperties.sessionid", "{" + str(uuid.uuid4()) + "}")
    root.set("root", str(output.parent))


def _place(
    root: ET.Element,
    by_id: dict[str | None, ET.Element],
    producer: ET.Element,
    common: dict[str, str],
    out_clock: str,
    start_clock: str,
    has_audio: bool,
    has_video: bool,
) -> tuple[list[str], list[str], int]:
    """Insert timeline chains and entries on A1/V1. Returns (labels, group leaves, next id)."""
    placed: list[str] = []
    leaves: list[str] = []
    next_chain = 0
    for wanted, (tractor_id, playlist_id, group_index), label, test in (
        (has_audio, AUDIO_TRACK, "A1", {"set.test_audio": "0", "set.test_image": "1"}),
        (has_video, VIDEO_TRACK, "V1", {"set.test_audio": "1", "set.test_image": "0"}),
    ):
        if not wanted:
            continue
        chain_id = f"chain{next_chain}"
        next_chain += 1
        overrides = dict(common, **(test if has_audio and has_video else {}))
        playlist = by_id[playlist_id]
        root.insert(list(root).index(playlist), _chain(chain_id, producer, out_clock, overrides))
        entry = ET.SubElement(
            playlist, "entry", {"in": start_clock, "out": out_clock, "producer": chain_id}
        )
        _prop(entry, "kdenlive:id", common["kdenlive:id"])
        by_id[tractor_id].set("out", out_clock)
        placed.append(label)
        leaves.append(f"{group_index}:0:-1")
    return placed, leaves, next_chain


def _set_durations(
    root: ET.Element,
    sequence: ET.Element,
    main_bin: ET.Element,
    length: int,
    rate: FrameRate,
    out_clock: str,
    has_audio: bool,
    has_video: bool,
    leaves: list[str],
) -> None:
    sequence.set("out", out_clock)
    for name, value in (
        ("kdenlive:duration", frames_to_clock(length, rate)),
        ("kdenlive:maxduration", str(length)),
        ("kdenlive:sequenceproperties.hasAudio", "1" if has_audio else "0"),
        ("kdenlive:sequenceproperties.hasVideo", "1" if has_video else "0"),
    ):
        _set_prop(sequence, name, value)
    if len(leaves) == 2:  # audio + video of one clip, grouped like Kdenlive (AVSplit)
        children = ",".join(
            f'{{"data": "{leaf}", "leaf": "clip", "type": "Leaf"}}' for leaf in reversed(leaves)
        )
        _set_prop(
            sequence,
            "kdenlive:sequenceproperties.groups",
            f'[{{"children": [{children}], "type": "AVSplit"}}]',
        )
    project_tractor = [el for el in root if el.tag == "tractor"][-1]
    project_tractor.set("out", out_clock)
    for track in project_tractor.findall("track"):
        track.set("out", out_clock)
    for entry in main_bin.findall("entry"):
        if entry.get("producer") == sequence.get("id"):
            entry.set("out", out_clock)


def build_project(
    output: Path, media_path: Path, profile_name: str, env: Mapping[str, str] | None = None
) -> tuple[ET.Element, CreateResult]:
    profile = read_profile(profile_name, env)
    rate = FrameRate(int(profile["frame_rate_num"]), int(profile["frame_rate_den"]))
    producer = melt_producer(media_path, profile_name, env)
    pprops = kdenlive_doc.props(producer)
    length = int(pprops.get("length", "0") or 0)
    has_video = int(pprops.get("video_index", "-1") or -1) >= 0
    has_audio = int(pprops.get("audio_index", "-1") or -1) >= 0
    if length <= 0 or not (has_video or has_audio):
        raise ProjectError("media_unreadable", f"{media_path} has no playable audio or video.")
    root = _load_template()
    by_id = {el.get("id"): el for el in root if el.get("id")}
    main_bin = by_id["main_bin"]
    _apply_profile(root, main_bin, profile, profile_name, output)

    out_clock = frames_to_clock(length - 1, rate)
    start_clock = frames_to_clock(0, rate)
    reference = _media_reference(media_path, output.parent)
    file_hash, file_size = kdenlive_file_hash(media_path)
    common = {
        "resource": reference,
        "mlt_service": "avformat-novalidate" if has_video else "avformat",
        "kdenlive:folderid": "-1",
        "kdenlive:id": "4",
        "kdenlive:control_uuid": "{" + str(uuid.uuid4()) + "}",
        "kdenlive:clip_type": "0" if (has_video and has_audio) else ("1" if has_audio else "2"),
        "kdenlive:file_size": str(file_size),
        "kdenlive:file_hash": file_hash,
        "eof": "pause",
        "mute_on_pause": "0",
        "length": str(length),
    }
    sequence = next(
        el for el in root if el.tag == "tractor" and kdenlive_doc.props(el).get("kdenlive:uuid")
    )
    placed, leaves, next_chain = _place(
        root, by_id, producer, common, out_clock, start_clock, has_audio, has_video
    )
    bin_chain = _chain(f"chain{next_chain}", producer, out_clock, common)
    root.insert(list(root).index(main_bin), bin_chain)
    bin_entry = ET.Element(
        "entry", {"in": start_clock, "out": out_clock, "producer": bin_chain.get("id", "")}
    )
    first_entry = next((i for i, el in enumerate(main_bin) if el.tag == "entry"), len(main_bin))
    main_bin.insert(first_entry + 1, bin_entry)
    _set_durations(root, sequence, main_bin, length, rate, out_clock, has_audio, has_video, leaves)
    result = CreateResult(
        project=str(output),
        project_sha256="",
        profile=profile_name,
        frame_rate=str(rate),
        media=str(media_path),
        media_reference=reference,
        clip_duration_frames=length,
        tracks=placed,
        kdenlive_format=f"document 1.1 (modeled on Kdenlive {TEMPLATE_KDENLIVE_VERSION})",
        warnings=[],
        notes=[],
    )
    return root, result


def serialize(root: ET.Element) -> bytes:
    ET.indent(root, space=" ")
    body = ET.tostring(root, encoding="unicode")
    return ("<?xml version='1.0' encoding='utf-8'?>\n" + body + "\n").encode("utf-8")


def write_atomically(path: Path, data: bytes, *, overwrite: bool) -> None:
    """Write via a temporary file in the same directory, then rename (no partial files)."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with tmp.open("xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if overwrite:
            tmp.replace(path)
        else:
            try:
                os.link(tmp, path)  # fails if `path` appeared meanwhile: never clobber
            except FileExistsError as exc:
                raise ProjectError(
                    "output_exists", f"{path} appeared while writing; not replaced."
                ) from exc
    finally:
        tmp.unlink(missing_ok=True)


def create_project(
    output: str,
    media_file: str,
    profile: str = "auto",
    *,
    overwrite: bool = False,
    env: Mapping[str, str] | None = None,
) -> CreateResult:
    out = Path(output).expanduser()
    if not output or not out.is_absolute() or out.suffix != ".kdenlive":
        raise ProjectError(
            "invalid_output", f"{output!r} must be an absolute path ending in .kdenlive."
        )
    if out.is_dir():
        raise ProjectError("invalid_output", f"{out} is a directory.")
    policy.check_write_target(out, "Creating a project")
    if out.exists() and not overwrite:
        raise ProjectError(
            "output_exists",
            f"{out} already exists.",
            "Choose a new path, or pass overwrite=true to replace it.",
        )
    if not out.parent.is_dir() or not os.access(out.parent, os.W_OK | os.X_OK):
        raise ProjectError("invalid_output", f"Cannot create files in {out.parent}.")
    checked = media.inspect_file(media_file)
    if checked.status is not media.MediaStatus.OK:
        raise ProjectError(
            "media_unreadable",
            f"{media_file}: {checked.status.value}"
            + (f" ({checked.detail})" if checked.detail else ""),
        )
    media_path = Path(checked.path)
    if media_path.resolve() == out.resolve():
        raise ProjectError("invalid_output", "The output path is the media file.")
    warnings = presence.check_not_open(out)
    if profile == "auto":
        if checked.probe is None or not checked.probe.ok:
            raise ProjectError(
                "profile_required",
                "The media could not be probed to pick a profile.",
                "Pass an explicit profile name (see list_profiles).",
            )
        profile = matching_profile(checked.probe, env)
    root, result = build_project(out, media_path, profile, env)
    data = serialize(root)
    write_atomically(out, data, overwrite=overwrite)
    # Read back with the same parser used for inspection: the file must describe what we built.
    doc = kdenlive_doc.load(str(out))
    timeline = project.timeline(doc)
    clips = [c for t in timeline.tracks for c in t.clips]
    if not clips or any(c.duration != result.clip_duration_frames for c in clips):
        raise ProjectError("internal_error", "The written project does not read back as built.")
    if Path(result.media_reference).is_absolute():
        warnings.append(
            "The media is outside the project directory, so its absolute path is stored; "
            "moving the media breaks the project (use relink later)."
        )
    result.project_sha256 = doc.info.sha256
    result.warnings = warnings
    result.notes = [
        "Created from a Kdenlive 26.08.2 project skeleton; open it in Kdenlive to edit.",
        "Verified by re-reading the file; opening in Kdenlive is verified separately (see docs).",
    ]
    return result
