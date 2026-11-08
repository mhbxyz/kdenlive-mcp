"""Reading Kdenlive clip effects that the server understands (pure functions, no I/O).

Used by the reader (`project.py`), the timeline operations (fades follow trims and splits) and
the audio tools (`audio.py`). Formats are those written by Kdenlive 26.08.2 (see audio.py).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable

from mcp_kdenlive.kdenlive_doc import props

# Effects anchored to a clip edge: their in/out follow the clip when it is trimmed or split.
START_FADES = ("fadein", "fade_from_black")
END_FADES = ("fadeout", "fade_to_black")

TimeParser = Callable[..., int]


def effects(entry: ET.Element, kdenlive_id: str) -> list[ET.Element]:
    return [f for f in entry.findall("filter") if props(f).get("kdenlive_id") == kdenlive_id]


def keyframes(value: str) -> list[tuple[str, str]]:
    """'0=-6;30=-20' → [('0', '-6'), ('30', '-20')]; a plain value is one keyframe at 0."""
    pairs = []
    for part in value.split(";"):
        if not part.strip():
            continue
        position, sep, level = part.partition("=")
        pairs.append((position.strip(), level.strip()) if sep else ("0", position.strip()))
    return pairs


def level_db(entry: ET.Element) -> float | None:
    """The constant level of the clip's volume effect, or None (none, animated, repeated)."""
    found = effects(entry, "volume")
    if len(found) != 1:
        return None
    frames = keyframes(props(found[0]).get("level", "0"))
    if len(frames) != 1:
        return None
    try:
        return float(frames[0][1].removesuffix("dB").strip())
    except ValueError:
        return None


def fade_length(entry: ET.Element, kdenlive_id: str, time: TimeParser) -> int:
    """Length in frames of an edge fade (out - in), 0 when absent."""
    found = effects(entry, kdenlive_id)
    if not found:
        return 0
    start = time(found[0].get("in"), default=0, what="fade in")
    end = time(found[0].get("out"), default=start, what="fade out")
    return max(end - start, 0)
