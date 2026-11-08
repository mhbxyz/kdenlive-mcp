"""Hardened XML parsing for untrusted project files.

Documents are parsed with expat directly into an ElementTree, **without namespace
processing** (Kdenlive files may use an undeclared ``kdenlive:`` prefix), refusing any
document type declaration, entity declaration or external reference, and bounding the input
size. Comments and processing instructions are kept so unrelated content can round-trip.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Never
from xml.parsers import expat

DEFAULT_MAX_BYTES = 64 * 1024 * 1024
MAX_DEPTH = 256  # real projects nest < 10 levels
MAX_ELEMENTS = 2_000_000  # a 64 MiB project has far fewer

ParseError = ET.ParseError


class UnsafeXMLError(ValueError):
    """The document uses XML features that are refused for safety (DTD, entities)."""


class XMLTooLargeError(ValueError):
    """The document exceeds the configured size limit."""


def _forbid(*_args: object) -> Never:
    raise UnsafeXMLError("DTDs, entity declarations and external references are not allowed")


def parse_bytes(data: bytes) -> ET.Element:
    """Parse a complete document. Raises ParseError or UnsafeXMLError."""
    builder = ET.TreeBuilder(insert_comments=True, insert_pis=True)
    parser = expat.ParserCreate()
    parser.buffer_text = True
    parser.StartDoctypeDeclHandler = _forbid
    parser.EntityDeclHandler = _forbid
    parser.UnparsedEntityDeclHandler = _forbid
    parser.ExternalEntityRefHandler = _forbid
    parser.NotationDeclHandler = _forbid
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    depth = 0
    count = 0

    def start(tag: str, attrs: dict[str, str]) -> None:
        nonlocal depth, count
        depth += 1
        count += 1
        if depth > MAX_DEPTH:
            raise XMLTooLargeError(f"XML nesting deeper than {MAX_DEPTH} levels")
        if count > MAX_ELEMENTS:
            raise XMLTooLargeError(f"more than {MAX_ELEMENTS} XML elements")
        builder.start(tag, attrs)

    def end(tag: str) -> None:
        nonlocal depth
        depth -= 1
        builder.end(tag)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = builder.data
    parser.CommentHandler = builder.comment
    parser.ProcessingInstructionHandler = builder.pi
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        err = ParseError(f"{expat.ErrorString(exc.code)}: line {exc.lineno}, column {exc.offset}")
        err.position = (exc.lineno, exc.offset)
        raise err from None
    root = builder.close()
    if root is None:  # pragma: no cover - expat rejects empty documents
        raise ParseError("no element found")
    return root


def parse_file(path: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> tuple[ET.Element, bytes]:
    """Read and parse `path` (at most `max_bytes`). Returns the root element and raw bytes."""
    with path.open("rb") as fh:
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise XMLTooLargeError(f"{path} is larger than {max_bytes} bytes")
    return parse_bytes(data), data
