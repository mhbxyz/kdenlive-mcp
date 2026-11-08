"""Safe writing of edited Kdenlive projects (the base of every editing tool).

Guarantees:

* **Copies by default.** Writing over the source requires ``overwrite=True`` *and* the
  ``expected_sha256`` of the version that was read; a different destination that already
  exists also requires ``overwrite=True`` and is backed up first.
* **Conflict detection.** The source is re-hashed just before writing; if it changed since it
  was read (another tool, or Kdenlive saving), the write is refused with ``[conflict]``. A
  tiny race window between that check and the rename remains (no portable file locking).
* **Faithful round trip.** Documents keep everything the parser saw — unknown elements,
  properties, attribute order, comments, whitespace — and serialization is verified: the bytes
  written must parse back to the same canonical XML (C14N) as the edited tree, and must load as a
  Kdenlive project. Unchanged projects are written byte-for-byte except ``<a></a>`` → ``<a/>``.
* **Atomic writes.** Data goes to a temporary file in the destination directory, is fsynced,
  then linked (new file, never clobbering) or renamed (overwrite) into place.
* **Relative media stay valid.** When the destination directory differs from the project's
  root, relative references are rewritten for the new location (or made absolute).
* **Open projects.** Kdenlive keeps projects in memory and overwrites the file when it saves;
  external edits to an open project are lost. Detection is best effort (process arguments,
  autosave locks); writing over a project that appears open is refused.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import secrets
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from mcp_kdenlive import kdenlive_doc, policy, presence, safexml
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import KdenliveDocument, props
from mcp_kdenlive.media import ASSET_FILE_PROPERTIES, URL_RE, _Resolver

# Properties holding file references that must follow the project when it moves.
PATH_PROPERTIES = ("resource", "warp_resource", "kdenlive:originalurl", "kdenlive:proxy")
GENERATED_RESOURCES = re.compile(r"^(black|0x[0-9a-fA-F]{6,8}|#.*|<producer>|-|)$")


@dataclass
class EditableProject:
    """A project loaded for modification. `root` is edited in place by the editing tools."""

    doc: KdenliveDocument
    original: bytes
    sha256: str
    changes: list[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return self.doc.path

    @property
    def root(self) -> ET.Element:
        return self.doc.root


class SaveResult(BaseModel):
    destination: str
    sha256: str
    size_bytes: int
    source: str
    source_sha256: str = Field(description="Revision of the source that was edited.")
    changes: list[str] = Field(description="Human-readable list of modifications applied.")
    rebased_paths: list[str] = Field(description="References rewritten for the new location.")
    backup: str | None = Field(description="Copy of the replaced file, when one was replaced.")
    warnings: list[str]
    notes: list[str]


# ---------------------------------------------------------------- loading ------------
def open_for_edit(path: str) -> EditableProject:
    doc = kdenlive_doc.load(path)
    original = doc.path.read_bytes()
    if hashlib.sha256(original).hexdigest() != doc.info.sha256:
        raise ProjectError("conflict", f"{doc.path} changed while it was being read.")
    if doc.generation < 5:
        raise ProjectError(
            "unsupported_structure",
            f"Editing requires a Kdenlive 23.04+ project (document version 1.1); this file is "
            f"{doc.document_version!r}.",
            "Open and save it once in a recent Kdenlive, then retry.",
        )
    return EditableProject(doc=doc, original=original, sha256=doc.info.sha256)


# ---------------------------------------------------------------- serialization ------
def serialize(project: EditableProject) -> bytes:
    raw = project.original
    head = raw[: raw.index(b"?>") + 2].decode("utf-8") + "\n" if raw.startswith(b"<?xml") else ""
    body = ET.tostring(project.root, encoding="unicode").replace(" />", "/>")
    tail = "\n" if raw.endswith(b"\n") else ""
    return (head + body + tail).encode("utf-8")


def _canonical(data: bytes | ET.Element) -> str:
    text = (
        data.decode("utf-8") if isinstance(data, bytes) else ET.tostring(data, encoding="unicode")
    )
    return ET.canonicalize(text, strip_text=False)


def verify_serialization(project: EditableProject, data: bytes) -> None:
    try:
        reparsed = safexml.parse_bytes(data)
    except (safexml.ParseError, safexml.UnsafeXMLError) as exc:  # pragma: no cover - defensive
        raise ProjectError("internal_error", f"Serialized project is not valid XML: {exc}") from exc
    if _canonical(reparsed) != _canonical(project.root):
        raise ProjectError("internal_error", "Serialization would not round-trip; nothing written.")


# ---------------------------------------------------------------- open detection -----
# Detection lives in presence.py; re-exported for callers and tests.
KdenliveUsage = presence.KdenliveUsage
kdenlive_usage = presence.kdenlive_usage


# ---------------------------------------------------------------- path rebasing ------
def _is_file_reference(value: str) -> bool:
    return (
        bool(value)
        and not GENERATED_RESOURCES.match(value)
        and not (URL_RE.match(value) and not value.startswith("file://"))
    )


FILE_SERVICES = ("avformat", "qimage", "pixbuf", "timewarp", "xml", "glaxnimate")


def _relative_path_properties(root: ET.Element) -> list[ET.Element]:
    """<property> elements holding relative file references (media, proxies, asset files)."""
    found: list[ET.Element] = []
    for element in root.iter():
        if element.tag not in ("producer", "chain", "filter", "transition"):
            continue
        p = props(element)
        names = set(PATH_PROPERTIES)
        tag_pairs = ASSET_FILE_PROPERTIES.get(element.tag, {})
        if p.get("mlt_service") in tag_pairs:
            names.add(tag_pairs[p["mlt_service"]])
        if element.tag in ("producer", "chain") and not p.get("mlt_service", "").startswith(
            FILE_SERVICES
        ):
            names.discard("resource")
        for prop in element.findall("property"):
            value = prop.text or ""
            if (
                prop.get("name") in names
                and _is_file_reference(value)
                and not Path(value).is_absolute()
                and not value.startswith(("file://", "$CURRENTPATH"))
            ):
                found.append(prop)
    return found


def rebase_paths(project: EditableProject, destination_dir: Path) -> list[str]:
    """Rewrite relative references so they stay valid from `destination_dir`."""
    resolver = _Resolver(project.doc)
    new_base = destination_dir.resolve()
    if Path(resolver.effective).resolve() == new_base:
        return []
    rewritten: list[str] = []
    for prop in _relative_path_properties(project.root):
        value = prop.text or ""
        absolute, _ = resolver.resolve(value)
        new_value = os.path.relpath(absolute, new_base)
        if new_value.startswith(".." + os.sep + ".." + os.sep):
            new_value = str(absolute)  # far away: an absolute path is clearer than ../../..
        if new_value != value:
            prop.text = new_value
            rewritten.append(f"{prop.get('name')}: {value} -> {new_value}")
    project.root.set("root", str(new_base))
    return rewritten


# ---------------------------------------------------------------- writing ------------
def _fsync_dir(directory: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write_atomically(path: Path, data: bytes, *, replace: bool) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with tmp.open("xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if replace:
            tmp.replace(path)
        else:
            try:
                os.link(tmp, path)
            except FileExistsError as exc:
                raise ProjectError(
                    "output_exists", f"{path} appeared while writing; it was not replaced."
                ) from exc
        _fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def _check_destination(
    project: EditableProject, destination: str, overwrite: bool, expected_sha256: str | None
) -> Path:
    dest = Path(destination).expanduser()
    if not destination or not dest.is_absolute() or dest.suffix != ".kdenlive":
        raise ProjectError("invalid_output", f"{destination!r} must be an absolute .kdenlive path.")
    if dest.is_dir():
        raise ProjectError("invalid_output", f"{dest} is a directory.")
    policy.check_write_target(dest, "Saving a project")
    if not dest.parent.is_dir() or not os.access(dest.parent, os.W_OK | os.X_OK):
        raise ProjectError("invalid_output", f"Cannot create files in {dest.parent}.")
    same_file = dest.exists() and dest.resolve() == project.path.resolve()
    if same_file:
        if not overwrite or not expected_sha256:
            raise ProjectError(
                "source_protected",
                "Refusing to write over the source project.",
                "Write to a new destination, or pass overwrite=true together with the "
                "expected_sha256 returned when the project was read.",
            )
        if expected_sha256 != project.sha256:
            raise ProjectError(
                "conflict", "expected_sha256 does not match the project that was read."
            )
    elif dest.exists() and not overwrite:
        raise ProjectError(
            "output_exists",
            f"{dest} already exists.",
            "Choose a new destination, or pass overwrite=true.",
        )
    return dest


def save(
    project: EditableProject,
    destination: str,
    *,
    overwrite: bool = False,
    expected_sha256: str | None = None,
) -> SaveResult:
    dest = _check_destination(project, destination, overwrite, expected_sha256)
    warnings = presence.check_not_open(dest)
    source_usage = kdenlive_usage(project.path)
    if source_usage.open:
        warnings.append(
            "The source appears to be open in Kdenlive; its unsaved changes are not "
            "included, and Kdenlive may overwrite the source when it saves."
        )

    rebased = rebase_paths(project, dest.parent)
    data = serialize(project)
    verify_serialization(project, data)
    # Conflict check as late as possible: the source must still be the revision we edited.
    try:
        current = hashlib.sha256(project.path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ProjectError("conflict", f"The source can no longer be read: {exc}.") from exc
    if current != project.sha256:
        raise ProjectError(
            "conflict",
            f"{project.path} changed since it was read; nothing written.",
            "Read the project again and reapply the edit.",
        )
    backup = None
    if dest.exists():
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = dest.with_name(f"{dest.stem}.backup-{stamp}.kdenlive")
        shutil.copy2(dest, backup_path)
        backup = str(backup_path)
    _write_atomically(dest, data, replace=dest.exists())
    reloaded = kdenlive_doc.load(str(dest))  # must still be a readable Kdenlive project
    return SaveResult(
        destination=str(dest),
        sha256=reloaded.info.sha256,
        size_bytes=reloaded.info.size,
        source=str(project.path),
        source_sha256=project.sha256,
        changes=list(project.changes),
        rebased_paths=rebased,
        backup=backup,
        warnings=warnings,
        notes=[
            "Kdenlive keeps open projects in memory: if the destination is open in Kdenlive, "
            "reload it there (File > Revert) before editing, or Kdenlive will overwrite it.",
        ],
    )
