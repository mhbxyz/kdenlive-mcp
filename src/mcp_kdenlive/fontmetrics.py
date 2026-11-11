"""Text widths from font files, to place titles the way Kdenlive's title editor does (#15).

Kdenlive's title editor puts a text item at its stored position with the width of the text
itself: the stored `box-width` only matters for `%s` template items. A title the server centres
must therefore be written at x = (frame - text width) / 2, which needs the text's width.

This reads the advance widths of the font's glyphs (`cmap`, `hmtx`) from the file fontconfig
picks, with the standard library only. Kerning and shaping are ignored: on Kdenlive's own
titles the result is within about 1 px per line (Noto Sans, 36 and 90 px). Anything unexpected
(no fontconfig, collections, unknown formats, damaged files) gives None, and the caller falls
back to a box across the safe area.
"""

from __future__ import annotations

import os
import shutil
import struct
from functools import lru_cache
from pathlib import Path

from mcp_kdenlive.policy import child_env
from mcp_kdenlive.process import ProcessTimeout, run

MAX_FONT_BYTES = 64 * 1024 * 1024


def font_file(family: str, *, italic: bool) -> Path | None:
    """The file fontconfig uses for `family` (regular or italic), or None."""
    fc_match = shutil.which("fc-match", path=child_env().get("PATH", os.defpath))
    if fc_match is None:
        return None
    pattern = f"{family}:style={'Italic' if italic else 'Regular'}"
    try:
        done = run([fc_match, "--format", "%{file}", pattern], timeout=30, env=child_env())
    except (OSError, ProcessTimeout):
        return None
    path = Path(done.stdout.strip()) if done.returncode == 0 else None
    return path if path and path.is_file() else None


def text_width(path: Path, size: float, text: str) -> float | None:
    """Width in pixels of the widest line of `text` at `size` pixels, or None."""
    metrics = _metrics(str(path))
    if metrics is None:
        return None
    units, advances, cmap = metrics
    last = len(advances) - 1

    def line_width(line: str) -> float:
        # glyphs past the last advance use it (the hmtx convention); unmapped ones use .notdef
        return sum(advances[min(cmap.get(ord(c), 0), last)] for c in line) * size / units

    return max(line_width(line) for line in text.split("\n"))


@lru_cache(maxsize=16)
def _metrics(path: str) -> tuple[int, list[int], dict[int, int]] | None:
    try:
        with Path(path).open("rb") as fh:
            data = fh.read(MAX_FONT_BYTES + 1)
        if len(data) > MAX_FONT_BYTES:
            return None
        return _parse(data)
    except (OSError, ValueError, struct.error, KeyError, IndexError, ZeroDivisionError):
        return None


def _parse(data: bytes) -> tuple[int, list[int], dict[int, int]] | None:
    if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        return None  # font collections (ttcf) and other formats are not read
    count = struct.unpack_from(">H", data, 4)[0]
    tables: dict[bytes, tuple[int, int]] = {}
    for i in range(count):
        tag, _, offset, length = struct.unpack_from(">4sIII", data, 12 + 16 * i)
        tables[tag] = (offset, length)
    units = struct.unpack_from(">H", data, tables[b"head"][0] + 18)[0]
    metrics = struct.unpack_from(">H", data, tables[b"hhea"][0] + 34)[0]
    hmtx = tables[b"hmtx"][0]
    advances = [struct.unpack_from(">H", data, hmtx + 4 * i)[0] for i in range(metrics)]
    cmap = _cmap(data, tables[b"cmap"][0])
    if not units or not advances or not cmap:
        return None
    return units, advances, cmap


def _cmap(data: bytes, base: int) -> dict[int, int]:
    """Unicode code point -> glyph, from a format 12 subtable, else a format 4 one."""
    subtables = struct.unpack_from(">H", data, base + 2)[0]
    offsets: dict[int, int] = {}
    for i in range(subtables):
        platform, encoding, offset = struct.unpack_from(">HHI", data, base + 4 + 8 * i)
        if (platform, encoding) in ((0, 3), (0, 4), (3, 1), (3, 10)):
            fmt = struct.unpack_from(">H", data, base + offset)[0]
            offsets.setdefault(fmt, base + offset)
    if 12 in offsets:
        return _format12(data, offsets[12])
    if 4 in offsets:
        return _format4(data, offsets[4])
    return {}


def _format12(data: bytes, at: int) -> dict[int, int]:
    groups = struct.unpack_from(">I", data, at + 12)[0]
    found: dict[int, int] = {}
    for i in range(groups):
        first, last, glyph = struct.unpack_from(">III", data, at + 16 + 12 * i)
        if last - first > 0x10FFFF:
            raise ValueError("bad cmap group")
        for code in range(first, last + 1):
            found[code] = glyph + code - first
    return found


def _format4(data: bytes, at: int) -> dict[int, int]:
    segments = struct.unpack_from(">H", data, at + 6)[0] // 2
    ends = struct.unpack_from(f">{segments}H", data, at + 14)
    starts_at = at + 16 + 2 * segments
    starts = struct.unpack_from(f">{segments}H", data, starts_at)
    deltas = struct.unpack_from(f">{segments}h", data, starts_at + 2 * segments)
    ranges_at = starts_at + 4 * segments
    ranges = struct.unpack_from(f">{segments}H", data, ranges_at)
    found: dict[int, int] = {}
    for k in range(segments):
        for code in range(starts[k], ends[k] + 1):
            if code == 0xFFFF:
                continue
            if ranges[k] == 0:
                glyph = (code + deltas[k]) & 0xFFFF
            else:
                where = ranges_at + 2 * k + ranges[k] + 2 * (code - starts[k])
                glyph = struct.unpack_from(">H", data, where)[0]
                glyph = (glyph + deltas[k]) & 0xFFFF if glyph else 0
            found[code] = glyph
    return found
