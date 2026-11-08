"""Basic titles (#15): an editable Kdenlive title clip in the bin, placed on a video track.

The clip is written like Kdenlive 26.08.2's title editor writes it ("Add as new Title"): a
`kdenlivetitle` producer with an empty `resource`, `kdenlive:clip_type` 2 and the title document
in `xmldata` (see tests/test_titles.py for the captured reference). Nothing is rasterized: the
title stays editable in Kdenlive's title editor, and MLT renders it with Qt and fontconfig.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import unicodedata
import uuid
import xml.etree.ElementTree as ET

from pydantic import BaseModel, Field

from mcp_kdenlive.edits import _next_bin_id, _next_id
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.policy import child_env
from mcp_kdenlive.process import ProcessTimeout, run
from mcp_kdenlive.timecode import frames_to_clock, frames_to_timecode
from mcp_kdenlive.timeline_edit import ClipPlacement, _open, insert_clip
from mcp_kdenlive.writer import EditableProject

__all__ = ["TitleBox", "TitleResult", "add_title", "installed_fonts"]

MAX_TEXT = 5000
DEFAULT_FONTS = ("Noto Sans", "DejaVu Sans", "Liberation Sans", "Cantarell", "Arial")
LINE_HEIGHT = 1.3625  # line height / pixel size measured on Kdenlive 26.08.2's own titles
EMOJI = range(0x1F000, 0x1FB00)  # pictographs and emoji: not drawn by MLT's title producer
MARGIN = 0.05  # presets keep 5 % of the frame free on every side
ALIGN = {"left": 1, "right": 2, "center": 4}  # Qt::AlignLeft, AlignRight, AlignHCenter
# Attributes Kdenlive 26.08.2 writes on a text item and that this server does not expose.
FIXED_CONTENT = {
    "font-underline": "0",
    "font-weight": "50",
    "letter-spacing": "0",
    "shadow": "0;#ff000000;21968;0;0",
    "tab-width": "80",
    "typewriter": "0;2;1;0;0",
}


class TitleBox(BaseModel):
    x: int = Field(description="Left edge of the text box, in pixels of the frame.")
    y: int = Field(description="Top edge of the text box (estimated height, see docs).")
    width: int
    height: int = Field(description="Estimated: lines x font size x 1.3625.")


class TitleResult(BaseModel):
    bin_id: str = Field(description="Bin clip id of the title (insert_clip can place it again).")
    name: str
    font: str
    font_checked: bool = Field(description="The font was found with fontconfig (fc-list).")
    box: TitleBox
    clips: list[ClipPlacement]
    warnings: list[str]


def installed_fonts() -> set[str] | None:
    """Font families known to fontconfig, or None when fc-list cannot be run.

    Not cached: a font installed while the server runs is found by the next call.
    """
    fc_list = shutil.which("fc-list", path=child_env().get("PATH", os.defpath))
    if fc_list is None:
        return None
    try:
        done = run([fc_list, "--format", "%{family}\n"], timeout=30, env=child_env())
    except (OSError, ProcessTimeout):
        return None
    if done.returncode != 0:
        return None
    return {name.strip() for line in done.stdout.splitlines() for name in line.split(",")} - {""}


def _font(requested: str | None, warnings: list[str]) -> tuple[str, bool]:
    fonts = installed_fonts()
    if fonts is None:
        warnings.append(
            "Fonts were not checked (fc-list could not be run); a missing font is replaced "
            "at render."
        )
        return requested or DEFAULT_FONTS[0], False
    if requested is None:
        found = next((f for f in DEFAULT_FONTS if f in fonts), None)
        if found is None:
            raise ProjectError(
                "dependency_missing",
                f"None of the default fonts {list(DEFAULT_FONTS)} is installed.",
                "Pass `font` with an installed family (fc-list : family).",
            )
        return found, True
    by_key = {f.casefold(): f for f in fonts}
    if requested.casefold() in by_key:
        return by_key[requested.casefold()], True
    close = difflib.get_close_matches(requested, sorted(fonts), n=5, cutoff=0.5)
    raise ProjectError(
        "dependency_missing",
        f"Font {requested!r} is not installed.",
        f"Close matches: {close}." if close else "List families with `fc-list : family`.",
    )


def _text(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise ProjectError("invalid_argument", "The title text is empty.")
    if len(text) > MAX_TEXT:
        raise ProjectError("invalid_argument", f"Title text is limited to {MAX_TEXT} characters.")
    # XML 1.0 cannot hold control characters, surrogates, U+FFFE and U+FFFF
    bad = [
        c
        for c in text
        if (c not in "\n\t" and unicodedata.category(c) in ("Cc", "Cs")) or c in "\ufffe\uffff"
    ]
    if bad:
        raise ProjectError(
            "invalid_argument", f"Control character {bad[0]!r} in the title text (use \\n)."
        )
    return text


def _rgba(value: str, what: str) -> str:
    match = re.fullmatch(r"#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?", value)
    if not match:
        raise ProjectError(
            "invalid_argument", f"{what} must be #RRGGBB or #RRGGBBAA, not {value!r}."
        )
    rgb = [int(match.group(1)[i : i + 2], 16) for i in (0, 2, 4)]
    alpha = int(match.group(2), 16) if match.group(2) else 255
    return ",".join(str(v) for v in (*rgb, alpha))


def _box(
    width: int,
    height: int,
    lines: int,
    font_size: int,
    vertical: str,
    x: int | None,
    y: int | None,
) -> TitleBox:
    box_height = round(lines * font_size * LINE_HEIGHT)
    if (x is None) != (y is None):
        raise ProjectError("invalid_argument", "Give both x and y, or neither.")
    if x is not None and y is not None:
        if not (0 <= x < width and 0 <= y < height):
            raise ProjectError(
                "invalid_argument", f"x and y must be inside the {width}x{height} frame."
            )
        if width - x < font_size:
            raise ProjectError(
                "invalid_range",
                f"At x={x} the text box is only {width - x} px wide for {font_size} px text.",
                "Use a smaller x, or align='right' with x near the left.",
            )
        return TitleBox(x=x, y=y, width=width - x, height=box_height)
    mx, my = round(width * MARGIN), round(height * MARGIN)
    if box_height > height - 2 * my:
        raise ProjectError(
            "invalid_range",
            f"{lines} line(s) of {font_size} px text (about {box_height} px) do not fit in the "
            f"{height} px frame.",
            "Use a smaller font_size or fewer lines.",
        )
    top = {
        "top": my,
        "center": (height - box_height) // 2,
        "bottom": height - my - box_height,
    }[vertical]
    return TitleBox(x=mx, y=top, width=width - 2 * mx, height=box_height)


def _document(
    text: str,
    frames: int,
    size: tuple[int, int],
    box: TitleBox,
    font: str,
    font_size: int,
    content_style: dict[str, str],
) -> str:
    width, height = size
    doc = ET.Element(
        "kdenlivetitle",
        {
            "LC_NUMERIC": "C",
            "duration": str(frames),
            "height": str(height),
            "out": str(frames - 1),
            "width": str(width),
        },
    )
    item = ET.SubElement(doc, "item", {"type": "QGraphicsTextItem", "z-index": "0"})
    position = ET.SubElement(item, "position", {"x": str(box.x), "y": str(box.y)})
    ET.SubElement(position, "transform").text = "1,0,0,0,1,0,0,0,1"
    attributes = {
        **content_style,
        "box-height": str(box.height),
        "box-width": str(box.width),
        "font": font,
        "font-pixel-size": str(font_size),
        **FIXED_CONTENT,
    }
    ET.SubElement(item, "content", dict(sorted(attributes.items()))).text = text
    viewport = f"0,0,{width},{height}"
    ET.SubElement(doc, "startviewport", {"rect": viewport})
    ET.SubElement(doc, "endviewport", {"rect": viewport})
    ET.SubElement(doc, "background", {"color": "0,0,0,0"})
    ET.indent(doc, " ")
    return ET.tostring(doc, encoding="unicode") + "\n"


def add_title(
    edit: EditableProject,
    text: str,
    track: str,
    start: int,
    *,
    duration: int | None = None,
    font: str | None = None,
    font_size: int | None = None,
    color: str = "#FFFFFF",
    outline: int = 0,
    outline_color: str = "#000000",
    align: str = "center",
    vertical: str = "center",
    x: int | None = None,
    y: int | None = None,
    italic: bool = False,
    sequence: str | None = None,
) -> TitleResult:
    """Create a title clip in the bin and place it on `track` at `start`."""
    text = _text(text)
    tl = _open(edit, None, sequence, needs_revision=False)
    rate = tl.doc.rate
    frames = round(5 * rate.num / rate.den) if duration is None else duration
    if frames < 1:
        raise ProjectError("invalid_argument", "duration must be at least 1 frame.")
    if align not in ALIGN or vertical not in ("top", "center", "bottom"):
        raise ProjectError(
            "invalid_argument",
            "align is left, center or right; vertical is top, center or bottom.",
        )
    profile = edit.root.find("profile")
    size = (
        int(profile.get("width", "0")) if profile is not None else 0,
        int(profile.get("height", "0")) if profile is not None else 0,
    )
    if min(size) <= 0:
        raise ProjectError("unsupported_structure", "The project profile has no frame size.")
    font_size = round(size[1] / 12) if font_size is None else font_size
    if not 1 <= font_size <= 1000:
        raise ProjectError("invalid_argument", "font_size must be between 1 and 1000 pixels.")
    if not 0 <= outline <= 50:
        raise ProjectError("invalid_argument", "outline must be between 0 and 50 pixels.")
    style = {
        "alignment": str(ALIGN[align]),
        "font-color": _rgba(color, "color"),
        "font-italic": "1" if italic else "0",
        "font-outline": str(outline),
        "font-outline-color": _rgba(outline_color, "outline_color"),
    }
    box = _box(*size, text.count("\n") + 1, font_size, vertical, x, y)
    warnings: list[str] = []
    family, checked = _font(font, warnings)
    emoji = sorted({c for c in text if ord(c) in EMOJI})
    if emoji:
        warnings.append(
            f"The text has emoji ({''.join(emoji[:10])}): MLT's title renderer draws nothing for "
            "them (seen with Kdenlive 26.08.2 and MLT 7.42)."
        )
    name = next(line.strip() for line in text.split("\n") if line.strip())[:80]
    xmldata = _document(text, frames, size, box, family, font_size, style)

    bin_id = str(_next_bin_id(edit.root))
    producer = ET.Element(
        "producer",
        {
            "id": _next_id(edit.root, "producer"),
            "in": frames_to_clock(0, rate),
            "out": frames_to_clock(frames - 1, rate),
        },
    )
    for key, value in (
        ("length", str(frames)),
        ("eof", "pause"),
        ("resource", ""),
        ("meta.media.progressive", "1"),
        ("aspect_ratio", "1"),
        ("seekable", "1"),
        ("mlt_service", "kdenlivetitle"),
        ("kdenlive:duration", frames_to_timecode(frames, rate)),
        ("kdenlive:clipname", name),
        ("xmldata", xmldata),
        ("kdenlive:folderid", "-1"),
        ("kdenlive:id", bin_id),
        ("kdenlive:control_uuid", "{" + str(uuid.uuid4()) + "}"),
        ("kdenlive:clip_type", "2"),
        ("kdenlive:uniqueId", "{" + str(uuid.uuid4()) + "}"),
        ("force_reload", "0"),
        ("meta.media.width", str(size[0])),
        ("meta.media.height", str(size[1])),
    ):
        ET.SubElement(producer, "property", {"name": key}).text = value
    producer.tail = "\n "
    main_bin = tl.doc.main_bin
    # Before every playlist that may play it, like Kdenlive (MLT resolves earlier ids only).
    first = next(i for i, el in enumerate(edit.root) if el.tag != "profile")
    edit.root.insert(first, producer)
    entry = ET.SubElement(
        main_bin,
        "entry",
        {
            "in": frames_to_clock(0, rate),
            "out": frames_to_clock(frames - 1, rate),
            "producer": producer.get("id", ""),
        },
    )
    entry.tail = "\n "
    tl.doc.by_id[producer.get("id", "")] = producer
    edit.changes.append(f"bin: added title {name!r} as clip {bin_id}")
    placed = insert_clip(edit, bin_id, track, start, audio_track=None, sequence=sequence)
    return TitleResult(
        bin_id=bin_id,
        name=name,
        font=family,
        font_checked=checked,
        box=box,
        clips=placed.clips,
        warnings=warnings,
    )
