"""Low-level, read-only access to a `.kdenlive` document (MLT XML + Kdenlive metadata).

This module knows the file structure (see docs/research/integration-surfaces.md §2.1):
profile, ``main_bin`` playlist, sequence tractors (``kdenlive:uuid``), track tractors made of
two playlists, and the final project tractor. Higher-level views live in ``project.py``.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from mcp_kdenlive import safexml
from mcp_kdenlive.timecode import FrameRate, TimeFormatError, parse_time

PRODUCER_TAGS = ("producer", "chain", "playlist", "tractor")


class ProjectError(Exception):
    """A project cannot be read. `code` is stable and machine-readable."""

    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        text = f"[{self.code}] {self.message}"
        return f"{text} Hint: {self.hint}" if self.hint else text


def props(element: ET.Element) -> dict[str, str]:
    """Direct `<property name=…>` children of an element (first occurrence wins)."""
    result: dict[str, str] = {}
    for prop in element.findall("property"):
        name = prop.get("name")
        if name is not None and name not in result:
            result[name] = prop.text or ""
    return result


@dataclass(frozen=True)
class FileInfo:
    path: str
    size: int
    sha256: str
    modified: str


@dataclass
class KdenliveDocument:
    """A parsed project file. Never written back by this class."""

    path: Path
    root: ET.Element
    info: FileInfo
    rate: FrameRate
    by_id: dict[str, ET.Element] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    # ---- document level -------------------------------------------------------------
    @property
    def main_bin(self) -> ET.Element:
        element = self.by_id.get("main_bin")
        if element is None:  # pragma: no cover - guaranteed by load()
            raise ProjectError("not_a_project", "main_bin playlist missing")
        return element

    @property
    def doc_properties(self) -> dict[str, str]:
        return props(self.main_bin)

    def doc_property(self, name: str) -> str | None:
        return self.doc_properties.get(f"kdenlive:docproperties.{name}")

    @property
    def document_version(self) -> str | None:
        return self.doc_property("version")

    @property
    def generation(self) -> int:
        """Kdenlive file-format generation: 5 for document version 1.1, 4 for 1.0x."""
        version = self.document_version or ""
        try:
            number = float(version)
        except ValueError:
            return 0
        if number >= 1.1:
            return 5
        if number >= 1.0:
            return 4
        return 3

    def time(self, value: str | None, *, default: int | None = None, what: str = "time") -> int:
        if value is None or value == "":
            if default is None:
                raise ProjectError("unsupported_structure", f"missing {what} value")
            return default
        try:
            return parse_time(value, self.rate)
        except TimeFormatError as exc:
            raise ProjectError("unsupported_structure", f"invalid {what}: {exc}") from exc

    # ---- sequences ------------------------------------------------------------------
    def sequence_tractors(self) -> list[ET.Element]:
        """Timeline tractors. Generation 5: tractors with kdenlive:uuid; older: main tractor."""
        found = [el for el in self.root if el.tag == "tractor" and props(el).get("kdenlive:uuid")]
        if found:
            return found
        tractors = [el for el in self.root if el.tag == "tractor"]
        return tractors[-1:] if tractors else []

    def active_sequence_uuid(self) -> str | None:
        return self.doc_property("activetimeline") or self.doc_property("uuid")


def _file_info(path: Path, data: bytes) -> FileInfo:
    stat = path.stat()
    return FileInfo(
        path=str(path),
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    )


def resolve_input_path(raw: str) -> Path:
    """Validate a user-supplied project path (absolute, existing regular file)."""
    if not raw or not raw.strip():
        raise ProjectError("invalid_path", "No path was given.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ProjectError(
            "invalid_path",
            f"{raw!r} is not an absolute path.",
            "Pass the absolute path of the .kdenlive file; the server's working directory is "
            "not meaningful to clients.",
        )
    try:
        if not path.exists():
            raise ProjectError("not_found", f"{path} does not exist.")
        if not path.is_file():
            raise ProjectError("not_a_file", f"{path} is not a regular file.")
    except PermissionError as exc:
        raise ProjectError("permission_denied", f"Cannot access {path}: {exc.strerror}.") from exc
    return path


def load(raw_path: str, *, max_bytes: int = safexml.DEFAULT_MAX_BYTES) -> KdenliveDocument:
    """Read and validate a Kdenlive project without modifying anything."""
    path = resolve_input_path(raw_path)
    try:
        root, data = safexml.parse_file(path, max_bytes=max_bytes)
    except PermissionError as exc:
        raise ProjectError("permission_denied", f"Cannot read {path}: {exc.strerror}.") from exc
    except safexml.XMLTooLargeError as exc:
        raise ProjectError(
            "too_large", str(exc), "Projects above the size limit are refused for safety."
        ) from exc
    except safexml.UnsafeXMLError as exc:
        raise ProjectError("unsafe_xml", f"{path}: {exc}.") from exc
    except safexml.ParseError as exc:
        raise ProjectError(
            "invalid_xml",
            f"{path} is not well-formed XML ({exc}).",
            "Is this really a .kdenlive project? It may be truncated or corrupted.",
        ) from exc
    if root.tag != "mlt":
        raise ProjectError(
            "not_a_project", f"{path} is XML but its root element is <{root.tag}>, not <mlt>."
        )
    by_id = {el.get("id", ""): el for el in root if el.tag in PRODUCER_TAGS and el.get("id")}
    if "main_bin" not in by_id:
        raise ProjectError(
            "not_a_project",
            f"{path} is MLT XML without Kdenlive project data (no 'main_bin' playlist).",
            "Plain MLT files (for example written by melt) are playable but are not Kdenlive "
            "projects.",
        )
    profile = root.find("profile")
    if profile is None:
        raise ProjectError("unsupported_structure", f"{path} has no <profile> element.")
    try:
        rate = FrameRate(
            int(profile.get("frame_rate_num", "0")), int(profile.get("frame_rate_den", "0"))
        )
    except ValueError as exc:
        raise ProjectError("unsupported_structure", f"invalid profile frame rate: {exc}") from exc
    doc = KdenliveDocument(
        path=path, root=root, info=_file_info(path, data), rate=rate, by_id=by_id
    )
    if doc.generation < 4:
        doc.warnings.append(
            f"Document version {doc.document_version!r} predates Kdenlive 20.08; results are "
            "best effort."
        )
    return doc
