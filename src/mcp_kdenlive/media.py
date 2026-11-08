"""Project media: reference resolution, missing-asset diagnostics and ffprobe metadata.

Path resolution follows Kdenlive 26.08.2 (documentvalidator.cpp / documentchecker.cpp):

* a relative ``resource`` is resolved against the ``<mlt root="…">`` directory;
* without a ``root`` attribute, the directory containing the project file is used;
* if ``root`` names a directory that no longer exists, the project is considered *moved*:
  the project file's directory is used instead, and absolute references that started with the
  old root are rewritten onto the new one;
* ``$CURRENTPATH`` (archived projects) stands for the project file's directory.

Nothing here modifies the project or the media, follows URLs, or invokes a shell.
"""

from __future__ import annotations

import json
import os
import re
import stat
import xml.etree.ElementTree as ET
from collections import Counter
from enum import StrEnum
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

from mcp_kdenlive.environment import locate
from mcp_kdenlive.kdenlive_doc import KdenliveDocument, ProjectError, props
from mcp_kdenlive.process import ProcessTimeout, run
from mcp_kdenlive.project import used_bin_ids

FILE_SERVICES = (
    "avformat",
    "avformat-novalidate",
    "qimage",
    "pixbuf",
    "xml",
    "timewarp",
    "glaxnimate",
)
GENERATED_SERVICES = (
    "color",
    "colour",
    "kdenlivetitle",
    "qtext",
    "blipflash",
    "count",
    "noise",
    "frei0r",
    "tone",
    "webvfx",
    "qml",
)
SEQUENCE_TAGS = ("tractor", "playlist")
URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
# Same pairs as Kdenlive's DocumentChecker (getAssetPairs/getLumaPairs), plus subtitles.
ASSET_FILE_PROPERTIES = {
    "filter": {
        "avfilter.lut3d": "av.file",
        "shape": "resource",
        "avfilter.subtitles": "av.filename",
    },
    "transition": {
        "luma": "resource",
        "movit.luma_mix": "resource",
        "composite": "luma",
        "region": "composite.luma",
    },
}
DEFAULT_MAX_ITEMS = 200
MAX_ITEMS_LIMIT = 5000
PROBE_TIMEOUT = 15.0
PROBE_LIMIT = 50


class MediaStatus(StrEnum):
    OK = "ok"
    """The file exists, is a regular file and is readable."""
    MISSING = "missing"
    """Nothing exists at the resolved path (or a symlink points nowhere)."""
    INACCESSIBLE = "inaccessible"
    """The path exists but cannot be read (permissions, or it is a directory)."""
    REMOTE = "remote"
    """A URL; never fetched."""
    GENERATED = "generated"
    """Produced by MLT/Kdenlive (color, title, generator, sequence): no file needed."""
    PATTERN = "pattern"
    """An image sequence pattern; only the containing directory is checked."""
    UNKNOWN = "unknown"
    """No resource or an unrecognised producer."""


class StreamInfo(BaseModel):
    index: int
    type: str
    codec: str | None
    width: int | None = None
    height: int | None = None
    frame_rate: str | None = Field(
        default=None, description="Rational r_frame_rate, e.g. 30000/1001."
    )
    rotation: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    duration_seconds: float | None = None


class ProbeInfo(BaseModel):
    ok: bool
    error: str | None = None
    format: str | None = None
    duration_seconds: float | None = None
    size_bytes: int | None = None
    streams: list[StreamInfo] = Field(default_factory=list)
    note: str = Field(
        default="ffprobe reads container headers; it does not prove every frame decodes.",
    )


class MediaItem(BaseModel):
    bin_id: str | None
    producer_id: str
    name: str | None
    service: str | None
    kind: str = Field(description="file, image, generated, sequence, playlist, remote, unknown.")
    reference: str | None = Field(description="Reference exactly as stored in the project.")
    resolved_path: str | None
    status: MediaStatus
    detail: str | None = None
    symlink_target: str | None = None
    rewritten_from_moved_root: bool = False
    original_reference: str | None = Field(
        default=None, description="kdenlive:originalurl: source media when a proxy is in use."
    )
    original_status: MediaStatus | None = None
    original_resolved_path: str | None = None
    proxy_reference: str | None = None
    proxy_status: MediaStatus | None = None
    used_in_timeline: bool
    probe: ProbeInfo | None = None


class AssetFile(BaseModel):
    """A file referenced by an effect or transition (LUT, luma/wipe image, subtitles)."""

    element: str = Field(description="'filter' or 'transition'.")
    element_id: str | None
    service: str
    property: str
    reference: str
    resolved_path: str
    status: MediaStatus


class RootInfo(BaseModel):
    project_dir: str
    declared: str | None = Field(description="The root attribute stored in the file.")
    declared_exists: bool | None
    effective: str = Field(description="Directory used to resolve relative references.")
    moved: bool = Field(description="True when the declared root no longer exists.")


class MediaReport(BaseModel):
    project: str
    root: RootInfo
    counts: dict[str, int]
    missing: list[str] = Field(description="Resolved paths (or references) of missing media.")
    items: list[MediaItem]
    asset_files: list[AssetFile] = Field(
        description="Files used by effects and transitions (checked like Kdenlive does)."
    )
    truncated: bool
    total_items: int
    probed: int
    notes: list[str]


# ------------------------------------------------------------------- resolution -------
class _Resolver:
    def __init__(self, doc: KdenliveDocument) -> None:
        self.project_dir = doc.path.parent
        declared = doc.root.get("root")
        self.declared = declared if declared not in (None, "") else None
        if self.declared is None or self.declared == "$CURRENTPATH":
            self.effective = self.project_dir
            self.declared_exists: bool | None = None if self.declared is None else True
            self.moved = False
        else:
            exists = Path(self.declared).is_dir()
            self.declared_exists = exists
            self.moved = not exists
            self.effective = Path(self.declared) if exists else self.project_dir

    def info(self) -> RootInfo:
        return RootInfo(
            project_dir=str(self.project_dir),
            declared=self.declared,
            declared_exists=self.declared_exists,
            effective=str(self.effective),
            moved=self.moved,
        )

    def resolve(self, reference: str) -> tuple[Path, bool]:
        """Return (absolute path, rewritten_from_moved_root)."""
        ref = reference.replace("$CURRENTPATH", str(self.project_dir))
        if ref.startswith("file://"):
            ref = ref[len("file://") :]
        path = Path(ref)
        if not path.is_absolute():
            return Path(os.path.normpath(self.effective / path)), False
        if self.moved and self.declared:
            old = self.declared.rstrip("/") + "/"
            if ref.startswith(old) and not path.exists():
                return Path(os.path.normpath(self.project_dir / ref[len(old) :])), True
        return Path(os.path.normpath(path)), False


def check_path(  # noqa: PLR0911 - one return per filesystem outcome
    path: Path, pattern: bool = False
) -> tuple[MediaStatus, str | None, str | None]:
    """Status, detail and symlink target of a local path. Never opens the file for writing."""
    target = None
    if pattern:
        parent = path.parent
        if parent.is_dir():
            return MediaStatus.PATTERN, f"Pattern; directory {parent} exists.", None
        return MediaStatus.MISSING, f"Pattern directory {parent} does not exist.", None
    try:
        if path.is_symlink():
            target = str(path.readlink())
        st = path.stat()
    except FileNotFoundError:
        if target is not None:
            return MediaStatus.MISSING, f"Broken symbolic link to {target}.", target
        return MediaStatus.MISSING, None, None
    except PermissionError:
        return MediaStatus.INACCESSIBLE, "Permission denied while checking the path.", target
    except OSError as exc:
        return MediaStatus.INACCESSIBLE, f"Cannot check the path: {exc.strerror}.", target
    if not stat.S_ISREG(st.st_mode):
        return MediaStatus.INACCESSIBLE, "Not a regular file.", target
    if not os.access(path, os.R_OK):
        return MediaStatus.INACCESSIBLE, "File is not readable by this user.", target
    return MediaStatus.OK, None, target


def _kind(service: str, tag: str) -> str:
    if tag == "tractor" or service == "tractor":
        return "sequence"
    if tag == "playlist":
        return "playlist"
    if service in ("qimage", "pixbuf"):
        return "image"
    if service in FILE_SERVICES:
        return "file"
    if service.startswith(GENERATED_SERVICES):
        return "generated"
    return "unknown"


def _file_reference(service: str, p: dict[str, str]) -> str | None:
    resource = p.get("resource")
    if service == "timewarp":
        if p.get("warp_resource"):
            return p["warp_resource"]
        if resource and ":" in resource:
            speed, _, rest = resource.partition(":")
            if re.fullmatch(r"-?[\d.]+", speed):
                return rest
    return resource


# ------------------------------------------------------------------- ffprobe ----------
def _ratio_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _float(value: object) -> float | None:
    try:
        return round(float(str(value)), 6)
    except (TypeError, ValueError):
        return None


def _rotation(stream: dict[str, object]) -> int | None:
    sides = stream.get("side_data_list")
    for side in sides if isinstance(sides, list) else []:
        if isinstance(side, dict) and "rotation" in side:
            return _ratio_int(side["rotation"])
    tags = stream.get("tags")
    if isinstance(tags, dict) and "rotate" in tags:
        return _ratio_int(tags["rotate"])
    return None


def probe_file(path: Path, ffprobe: str | None = None, timeout: float = PROBE_TIMEOUT) -> ProbeInfo:
    """Run ffprobe on one local file (file protocol only)."""
    exe = ffprobe or locate("ffprobe")
    if exe is None:
        return ProbeInfo(ok=False, error="ffprobe is not available; see diagnose_environment.")
    args = [
        exe,
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        f"file:{path}",
    ]
    try:
        done = run(args, timeout=timeout)
    except ProcessTimeout:
        return ProbeInfo(ok=False, error=f"ffprobe did not finish within {timeout:g}s.")
    except OSError as exc:
        return ProbeInfo(ok=False, error=f"Cannot start ffprobe: {exc.strerror or exc}.")
    if done.returncode != 0:
        message = (done.stderr.strip().splitlines() or ["unknown error"])[-1]
        return ProbeInfo(ok=False, error=f"ffprobe failed: {message[:300]}")
    try:
        data = json.loads(done.stdout)
    except json.JSONDecodeError:
        return ProbeInfo(ok=False, error="ffprobe returned invalid JSON.")
    fmt = data.get("format", {}) if isinstance(data, dict) else {}
    streams = []
    for s in data.get("streams", []) if isinstance(data, dict) else []:
        if not isinstance(s, dict):
            continue
        kind = str(s.get("codec_type", "unknown"))
        rate = s.get("r_frame_rate") if kind == "video" else None
        streams.append(
            StreamInfo(
                index=_ratio_int(s.get("index")) or 0,
                type=kind,
                codec=s.get("codec_name"),
                width=_ratio_int(s.get("width")),
                height=_ratio_int(s.get("height")),
                frame_rate=rate if rate and rate != "0/0" else None,
                rotation=_rotation(s) if kind == "video" else None,
                sample_rate=_ratio_int(s.get("sample_rate")),
                channels=_ratio_int(s.get("channels")),
                duration_seconds=_float(s.get("duration")),
            )
        )
    return ProbeInfo(
        ok=True,
        format=fmt.get("format_name"),
        duration_seconds=_float(fmt.get("duration")),
        size_bytes=_ratio_int(fmt.get("size")),
        streams=streams,
    )


class MediaFileReport(BaseModel):
    path: str
    status: MediaStatus
    detail: str | None
    symlink_target: str | None
    probe: ProbeInfo | None


def inspect_file(raw: str) -> MediaFileReport:
    """Check and probe one local media file given by absolute path (never a URL)."""
    if not raw or URL_RE.match(raw):
        raise ProjectError(
            "invalid_path",
            "Give an absolute local file path, not a URL.",
            "Remote media is never fetched by this server.",
        )
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ProjectError("invalid_path", f"{raw!r} is not an absolute path.")
    status, detail, target = check_path(path)
    return MediaFileReport(
        path=str(path),
        status=status,
        detail=detail,
        symlink_target=target,
        probe=probe_file(path) if status is MediaStatus.OK else None,
    )


# ------------------------------------------------------------------- report -----------
def _item(
    doc: KdenliveDocument,
    resolver: _Resolver,
    producer: ET.Element,
    is_sequence: bool,
    used: set[str],
) -> MediaItem:
    p = props(producer)
    service = p.get("mlt_service") or ""
    bin_id = p.get("kdenlive:id")
    kind = "sequence" if is_sequence else _kind(service, producer.tag)
    reference = _file_reference(service, p) if kind in ("file", "image") else p.get("resource")
    item = MediaItem(
        bin_id=bin_id,
        producer_id=producer.get("id") or "",
        name=p.get("kdenlive:clipname")
        or (PurePosixPath(reference).name if reference and kind in ("file", "image") else None),
        service=service or producer.tag,
        kind=kind,
        reference=reference,
        resolved_path=None,
        status=MediaStatus.UNKNOWN,
        used_in_timeline=bool(bin_id and bin_id in used),
    )
    if kind in ("generated", "sequence", "playlist") and not (kind == "playlist" and reference):
        item.status = MediaStatus.GENERATED
        return item
    if not reference:
        item.detail = "No resource stored for this clip."
        return item
    if URL_RE.match(reference) and not reference.startswith("file://"):
        item.kind, item.status = "remote", MediaStatus.REMOTE
        item.detail = "URL references are reported but never fetched."
        return item
    pattern = bool(re.search(r"%\d*d|\.all\.\w+$", reference))
    path, rewritten = resolver.resolve(reference)
    item.resolved_path, item.rewritten_from_moved_root = str(path), rewritten
    item.status, item.detail, item.symlink_target = check_path(path, pattern)
    original = p.get("kdenlive:originalurl")
    proxy = p.get("kdenlive:proxy")
    if proxy and proxy != "-":
        item.proxy_reference = proxy
        item.proxy_status = check_path(resolver.resolve(proxy)[0])[0]
    if original and original != reference:
        opath, _ = resolver.resolve(original)
        item.original_reference = original
        item.original_resolved_path = str(opath)
        item.original_status = check_path(opath)[0]
        if proxy and reference == proxy:
            # The stored resource is the proxy; the clip itself is the original media.
            item.detail = (
                f"Proxy in use; proxy {item.proxy_status.value if item.proxy_status else 'unknown'}"
                ", original " + item.original_status.value + ". Kdenlive rebuilds missing proxies."
            )
            item.status, item.resolved_path = item.original_status, str(opath)
    return item


def _asset_files(doc: KdenliveDocument, resolver: _Resolver) -> list[AssetFile]:
    found: list[AssetFile] = []
    seen: set[tuple[str, str]] = set()
    for tag, pairs in ASSET_FILE_PROPERTIES.items():
        for element in doc.root.iter(tag):
            p = props(element)
            service = p.get("mlt_service") or ""
            prop = pairs.get(service)
            reference = p.get(prop) if prop else None
            if not reference or (service == "avfilter.subtitles" and p.get("internal_added")):
                continue
            if URL_RE.match(reference) and not reference.startswith("file://"):
                continue
            key = (service, reference)
            if key in seen:
                continue
            seen.add(key)
            path, _ = resolver.resolve(reference)
            found.append(
                AssetFile(
                    element=tag,
                    element_id=element.get("id"),
                    service=service,
                    property=prop or "",
                    reference=reference,
                    resolved_path=str(path),
                    status=check_path(path)[0],
                )
            )
    return found


def media_report(
    doc: KdenliveDocument,
    *,
    probe: bool = False,
    max_items: int = DEFAULT_MAX_ITEMS,
    ffprobe: str | None = None,
) -> MediaReport:
    if not 1 <= max_items <= MAX_ITEMS_LIMIT:
        raise ProjectError(
            "invalid_argument", f"max_items must be between 1 and {MAX_ITEMS_LIMIT}."
        )
    resolver = _Resolver(doc)
    sequence_ids = {t.get("id") for t in doc.sequence_tractors()}
    main_bin_entries = doc.main_bin.findall("entry")
    timeline_used = used_bin_ids(doc)
    items: list[MediaItem] = []
    for entry in main_bin_entries:
        pid = entry.get("producer") or ""
        producer = doc.by_id.get(pid)
        if producer is None:
            doc.warnings.append(f"Bin entry references missing producer {pid!r}.")
            continue
        items.append(_item(doc, resolver, producer, pid in sequence_ids, timeline_used))
    total = len(items)
    kept = items[:max_items]
    probed = 0
    if probe:
        exe = ffprobe or locate("ffprobe")
        for item in kept:
            if probed >= PROBE_LIMIT:
                break
            path = item.resolved_path if item.status is MediaStatus.OK else None
            if item.kind in ("file", "image") and path:
                item.probe = probe_file(Path(path), exe)
                probed += 1
    assets = _asset_files(doc, resolver)
    counts = Counter(i.status.value for i in items)
    notes = [
        "Statuses come from filesystem checks only; 'ok' does not prove the media decodes.",
        "Relative references resolve against the project root as Kdenlive does; see 'root'.",
        "Fonts and images used inside titles, and installed effect plugins, are not checked.",
    ]
    if probe and probed >= PROBE_LIMIT:
        notes.append(f"Only the first {PROBE_LIMIT} files were probed.")
    if resolver.moved:
        notes.append(
            "The stored root no longer exists: the project was moved. References were "
            "resolved against the project file's directory, as Kdenlive does on load."
        )
    return MediaReport(
        project=str(doc.path),
        root=resolver.info(),
        counts=dict(sorted(counts.items())),
        missing=[
            i.resolved_path or i.reference or i.producer_id
            for i in items
            if i.status is MediaStatus.MISSING
        ]
        + [a.resolved_path for a in assets if a.status is MediaStatus.MISSING],
        items=kept,
        asset_files=assets,
        truncated=total > len(kept),
        total_items=total,
        probed=probed,
        notes=notes,
    )
