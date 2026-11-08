"""Subtitles (#16): read a sequence's subtitle track, import SRT and WebVTT files.

Kdenlive 26.08.2 stores the main sequence's subtitles in ``<project file>.ass`` next to the
project (written by Kdenlive in tests/fixtures/kd26/kd26-subtitle.kdenlive.ass), referenced by
an internal ``avfilter.subtitles`` filter on the sequence tractor (``av.filename``, relative)
and listed in ``kdenlive:sequenceproperties.subtitlesList``. The import writes the same file
next to the saved project (writer.save names it after the destination, like Kdenlive's Save As).
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from pydantic import BaseModel, Field

from mcp_kdenlive import policy
from mcp_kdenlive.edits import _next_id, _sequence, _set_prop
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import KdenliveDocument, props
from mcp_kdenlive.media import _Resolver
from mcp_kdenlive.timecode import FrameRate, frames_to_timecode
from mcp_kdenlive.writer import EditableProject, PendingFile

__all__ = ["SubtitleImport", "SubtitleReport", "import_subtitles", "list_subtitles"]

SERVICE = "avfilter.subtitles"
LIST_PROPERTY = "kdenlive:sequenceproperties.subtitlesList"
STYLES_PROPERTY = "kdenlive:sequenceproperties.globalSubtitleStyles"
MAX_BYTES = 10 * 1024 * 1024
MAX_CUES = 50_000
MAX_HOURS = 100  # ASS timestamps written by Kdenlive have two-digit hours
FORMATS = {".srt": "srt", ".vtt": "vtt"}


# ---------------------------------------------------------------- models ------------
class SubtitleEvent(BaseModel):
    start: int = Field(description="First frame on the timeline.")
    end: int = Field(description="Exclusive end frame.")
    start_timecode: str
    layer: int = Field(description="ASS layer: overlapping subtitles sit on different layers.")
    style: str
    text: str = Field(description="Text; lines separated by \\n. ASS override tags are kept.")


class SubtitleTrack(BaseModel):
    id: int
    name: str
    file: str = Field(description="Resolved path of the subtitle file (ASS).")
    reference: str = Field(description="av.filename as stored in the project.")
    status: str = Field(description="'ok', 'missing' or 'unreadable'.")
    styles: list[str]
    event_count: int
    events: list[SubtitleEvent]


class SubtitleReport(BaseModel):
    sequence_uuid: str | None
    tracks: list[SubtitleTrack]
    truncated: bool = Field(description="True when events beyond max_events were left out.")


class SubtitleImport(BaseModel):
    source: str
    format: str = Field(description="'srt' or 'vtt'.")
    events: int
    layers: int = Field(description="Layers used: overlapping cues go to the next free layer.")
    first_frame: int
    last_frame: int = Field(description="Exclusive end of the last subtitle.")
    replaced_events: int = Field(description="Events of the previous subtitles, now replaced.")
    warnings: list[str]


@dataclass
class Cue:
    start_ms: int
    end_ms: int
    text: str
    number: int  # 1-based cue position, for messages


# ---------------------------------------------------------------- time --------------
def _frames(ms: int, rate: FrameRate) -> int:
    return round(Fraction(ms, 1000) * rate.fps)


def _ass_time(frame: int, rate: FrameRate) -> str:
    centis = Fraction(frame) / rate.fps * 100
    cs = int(centis + Fraction(1, 2))  # half up, like Kdenlive (frame 210 at 29.97 -> 07.01)
    seconds, cc = divmod(cs, 100)
    minutes, ss = divmod(seconds, 60)
    hh, mm = divmod(minutes, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d}.{cc:02d}"


def _parse_ass_time(value: str, rate: FrameRate) -> int:
    match = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2})[.:](\d{1,3})", value.strip())
    if not match:
        raise ValueError(value)
    h, m, s, frac = match.groups()
    seconds = Fraction(int(h) * 3600 + int(m) * 60 + int(s)) + Fraction(int(frac), 10 ** len(frac))
    return round(seconds * rate.fps)


# ---------------------------------------------------------------- reading -----------
def _sequence_tractor(doc: KdenliveDocument, sequence: str | None) -> ET.Element | None:
    tractors = doc.sequence_tractors()
    wanted = sequence or doc.active_sequence_uuid()
    return next(
        (t for t in tractors if props(t).get("kdenlive:uuid") == wanted),
        tractors[0] if tractors else None,
    )


def _subtitle_filters(tractor: ET.Element) -> list[ET.Element]:
    return [f for f in tractor.findall("filter") if props(f).get("mlt_service") == SERVICE]


def _listed(tractor: ET.Element) -> list[dict[str, object]]:
    raw = props(tractor).get(LIST_PROPERTY, "").strip()
    try:
        data = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _read_ass(text: str, rate: FrameRate) -> tuple[list[str], list[SubtitleEvent]]:
    styles: list[str] = []
    events: list[SubtitleEvent] = []
    section, fmt = "", ["layer", "start", "end", "style", "name", "marginl", "marginr",
                        "marginv", "effect", "text"]  # fmt: skip
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.lower()
            continue
        key, _, value = line.partition(":")
        if section == "[v4+ styles]" and key.strip() == "Style":
            styles.append(value.split(",", 1)[0].strip())
        elif section == "[events]" and key.strip() == "Format":
            fmt = [f.strip().lower() for f in value.split(",")]
        elif section == "[events]" and key.strip() == "Dialogue":
            fields = value.lstrip().split(",", len(fmt) - 1)
            if len(fields) < len(fmt):
                continue
            row = dict(zip(fmt, fields, strict=True))
            try:
                start = _parse_ass_time(row["start"], rate)
                end = _parse_ass_time(row["end"], rate)
                layer = int(row.get("layer", "0") or 0)
            except (ValueError, KeyError):
                continue
            events.append(
                SubtitleEvent(
                    start=start,
                    end=end,
                    start_timecode=frames_to_timecode(start, rate),
                    layer=layer,
                    style=row.get("style", "").strip(),
                    text=row.get("text", "").replace("\\N", "\n").replace("\\n", "\n"),
                )
            )
    return styles, events


def list_subtitles(
    doc: KdenliveDocument, sequence: str | None = None, max_events: int = 500
) -> SubtitleReport:
    tractor = _sequence_tractor(doc, sequence)
    if tractor is None:
        return SubtitleReport(sequence_uuid=None, tracks=[], truncated=False)
    resolver = _Resolver(doc)
    names = {int(str(d.get("id", 0))): str(d.get("name", "Subtitles")) for d in _listed(tractor)}
    tracks, truncated = [], False
    for index, flt in enumerate(_subtitle_filters(tractor)):
        reference = props(flt).get("av.filename", "")
        resolved, _ = resolver.resolve(reference)
        path = Path(resolved)
        styles: list[str] = []
        events: list[SubtitleEvent] = []
        status = "ok"
        try:
            styles, events = _read_ass(path.read_text(encoding="utf-8-sig"), doc.rate)
        except FileNotFoundError:
            status = "missing"
        except (OSError, UnicodeDecodeError):
            status = "unreadable"
        truncated = truncated or len(events) > max_events
        tracks.append(
            SubtitleTrack(
                id=index,
                name=names.get(index, "Subtitles"),
                file=str(path),
                reference=reference,
                status=status,
                styles=styles,
                event_count=len(events),
                events=events[:max_events],
            )
        )
    return SubtitleReport(
        sequence_uuid=props(tractor).get("kdenlive:uuid"), tracks=tracks, truncated=truncated
    )


# ---------------------------------------------------------------- parsing -----------
def _decode(data: bytes, name: str) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectError(
            "invalid_argument",
            f"{name} is not UTF-8 (byte {exc.start}).",
            "Convert it first, e.g. `iconv -f latin1 -t utf-8 in.srt > out.srt` (UTF-16 needs a "
            "byte order mark).",
        ) from exc


SRT_TIME = r"(\d+):(\d{2}):(\d{2})[,.](\d{3})"
VTT_TIME = r"(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})"


def _ms(groups: tuple[str | None, ...]) -> int:
    h, m, s, ms = (int(g) if g else 0 for g in groups)
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def _blocks(text: str) -> list[tuple[int, list[str]]]:
    """Blank-line separated blocks with the line number where each starts."""
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start = 0
    for number, line in enumerate(text.split("\n"), 1):
        if line.strip():
            if not current:
                start = number
            current.append(line.rstrip())
        elif current:
            blocks.append((start, current))
            current = []
    if current:
        blocks.append((start, current))
    return blocks


def _timing(line: str, pattern: str, where: str) -> tuple[int, int, str]:
    match = re.match(rf"\s*{pattern}\s*-->\s*{pattern}(.*)$", line)
    if not match:
        raise ProjectError(
            "invalid_argument",
            f"{where}: expected a timing line like `00:00:01,000 --> 00:00:02,500`, "
            f"got {line.strip()[:80]!r}.",
        )
    groups = match.groups()
    half = (len(groups) - 1) // 2
    return _ms(groups[:half]), _ms(groups[half:-1]), groups[-1].strip()


def _parse_srt(text: str, name: str) -> tuple[list[Cue], list[str]]:
    cues: list[Cue] = []
    for start_line, lines in _blocks(text):
        timing = 1 if lines[0].strip().isdigit() and len(lines) > 1 else 0
        start, end, _rest = _timing(lines[timing], SRT_TIME, f"{name} line {start_line + timing}")
        cues.append(Cue(start, end, "\n".join(lines[timing + 1 :]), len(cues) + 1))
    return cues, []


def _parse_vtt(text: str, name: str) -> tuple[list[Cue], list[str]]:
    blocks = _blocks(text)
    if not blocks or not blocks[0][1][0].startswith("WEBVTT"):
        raise ProjectError("invalid_argument", f"{name} does not start with `WEBVTT`.")
    cues: list[Cue] = []
    settings = False
    for start_line, lines in blocks[1:]:
        if lines[0].startswith(("NOTE", "STYLE", "REGION")):
            continue
        timing = 0 if "-->" in lines[0] else 1
        if timing >= len(lines):
            raise ProjectError("invalid_argument", f"{name} line {start_line}: cue without timing.")
        start, end, rest = _timing(lines[timing], VTT_TIME, f"{name} line {start_line + timing}")
        settings = settings or bool(rest)
        body = "\n".join(lines[timing + 1 :])
        body = re.sub(r"</?(?:v|c|lang|ruby|rt)(?:[ .][^>]*)?>", "", body)
        cues.append(Cue(start, end, body, len(cues) + 1))
    warnings = ["WebVTT cue settings (position, alignment) are ignored."] if settings else []
    return cues, warnings


def _ass_text(cue: Cue, warnings: list[str]) -> str:
    text = cue.text
    if "{" in text or "}" in text:
        warnings.append(
            f"Cue {cue.number}: braces would hide text in ASS ({{...}} are style codes); "
            "they became parentheses."
        )
        text = text.replace("{", "(").replace("}", ")")
    for tag, code in (("i", "i"), ("b", "b"), ("u", "u")):
        text = re.sub(rf"<{tag}>", f"{{\\\\{code}1}}", text, flags=re.I)
        text = re.sub(rf"</{tag}>", f"{{\\\\{code}0}}", text, flags=re.I)
    stripped = re.sub(r"</?font[^>]*>", "", text, flags=re.I)
    if stripped != text:
        warnings.append(f"Cue {cue.number}: <font> colours and faces are not kept.")
    return stripped.replace("\n", "\\N")


# ---------------------------------------------------------------- writing -----------
def _ass_document(
    rows: list[tuple[int, str, str, str]], width: int, height: int, max_layer: int
) -> str:
    """The ASS file as Kdenlive 26.08.2 writes it (header copied from its own output)."""
    head = [
        "[Script Info]",
        "; Script generated by Kdenlive 26.08.2",
        f"LayoutResX: {width}",
        f"LayoutResY: {height}",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "YCbCr Matrix: None",
        "",
        "[Kdenlive Extradata]",
        f"MaxLayer: {max_layer}",
        "DefaultStyles: Default",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,40.00,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,"
        "100.00,100.00,0.00,0.00,1,1.00,0.00,2,40,40,26,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines = [
        f"Dialogue: {layer},{start},{end},Default,,0,0,0,,{text}"
        for layer, start, end, text in rows
    ]
    return "\n".join([*head, *lines]) + "\n"


def _layers(spans: list[tuple[int, int]]) -> list[int]:
    """First layer where each span does not overlap an earlier one (spans sorted by start)."""
    ends: list[int] = []
    result = []
    for start, end in spans:
        layer = next((i for i, e in enumerate(ends) if e <= start), len(ends))
        if layer == len(ends):
            ends.append(end)
        else:
            ends[layer] = end
        result.append(layer)
    return result


def _read_cues(subtitle_file: str) -> tuple[Path, str, list[Cue], list[str]]:
    path = policy.check_allowed(Path(subtitle_file).expanduser(), "Reading subtitles")
    kind = FORMATS.get(path.suffix.lower())
    if kind is None:
        raise ProjectError(
            "invalid_argument",
            f"{path.name}: only SubRip (.srt) and WebVTT (.vtt) files are imported.",
            "Convert other formats first, e.g. `ffmpeg -i in.ass out.srt` (ASS styling is "
            "then lost).",
        )
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ProjectError("invalid_argument", f"{path.name} is larger than 10 MB.")
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise ProjectError("not_found", f"{path} does not exist.") from exc
    except OSError as exc:
        raise ProjectError("invalid_path", f"Cannot read {path}: {exc.strerror}.") from exc
    text = _decode(data, path.name).replace("\r\n", "\n").replace("\r", "\n")
    cues, warnings = (_parse_srt if kind == "srt" else _parse_vtt)(text, path.name)
    if not cues:
        raise ProjectError("invalid_argument", f"{path.name} has no subtitle.")
    if len(cues) > MAX_CUES:
        raise ProjectError("invalid_argument", f"At most {MAX_CUES} subtitles per file.")
    return path, kind, cues, warnings


def _place(
    cues: list[Cue], rate: FrameRate, offset: int, name: str, warnings: list[str]
) -> list[tuple[int, int, Cue]]:
    """Timeline frames of each cue (rounded to the nearest frame), sorted by start."""
    spans: list[tuple[int, int, Cue]] = []
    short = 0
    for cue in cues:
        if cue.end_ms < cue.start_ms:
            raise ProjectError(
                "invalid_argument", f"{name}: subtitle {cue.number} ends before it starts."
            )
        if cue.end_ms >= MAX_HOURS * 3600 * 1000:
            raise ProjectError(
                "invalid_range", f"{name}: subtitle {cue.number} ends after {MAX_HOURS} hours."
            )
        start = _frames(cue.start_ms, rate) + offset
        end = _frames(cue.end_ms, rate) + offset
        if start < 0:
            raise ProjectError(
                "invalid_range",
                f"With offset {offset}, subtitle {cue.number} would start before frame 0.",
            )
        if end <= start:
            end, short = start + 1, short + 1
        spans.append((start, end, cue))
    if short:
        warnings.append(f"{short} subtitle(s) shorter than a frame now last one frame.")
    return sorted(spans, key=lambda span: (span[0], span[1]))


def _existing(
    edit: EditableProject, seq: ET.Element, replace: bool
) -> tuple[list[ET.Element], int]:
    """The sequence's subtitle filters and their event count; refused unless `replace`."""
    existing = _subtitle_filters(seq)
    if not existing and not _listed(seq):
        return [], 0
    current = list_subtitles(edit.doc, props(seq).get("kdenlive:uuid"))
    replaced = sum(t.event_count for t in current.tracks)
    if not replace:
        raise ProjectError(
            "subtitles_exist",
            f"The sequence already has subtitles ({replaced} event(s)); nothing was changed.",
            "Pass replace=true to replace them (the old file is not modified), or "
            "list_subtitles to read them.",
        )
    if len(existing) > 1 or len(_listed(seq)) > 1:
        raise ProjectError(
            "unsupported_structure",
            "The sequence has several subtitle tracks; replacing them is not supported.",
        )
    return existing, replaced


def _pointer(
    edit: EditableProject, seq: ET.Element, existing: list[ET.Element]
) -> Callable[[Path], None]:
    """Point the sequence at the subtitle file once writer.save knows its name."""

    def point(target: Path) -> None:
        name = str(_listed(seq)[0].get("name", "Subtitles")) if _listed(seq) else "Subtitles"
        for old in existing:
            seq.remove(old)
        flt = ET.SubElement(seq, "filter", {"id": _next_id(edit.root, "filter")})
        for key, value in (
            ("mlt_service", SERVICE),
            ("av.alpha", "1"),
            ("internal_added", "237"),
            ("av.filename", target.name),
        ):
            ET.SubElement(flt, "property", {"name": key}).text = value
        listed = [{"file": str(target), "id": 0, "name": name}]
        _set_prop(seq, LIST_PROPERTY, json.dumps(listed, indent=4, ensure_ascii=False) + "\n")
        if STYLES_PROPERTY not in props(seq):
            _set_prop(seq, STYLES_PROPERTY, "[\n]\n")

    return point


def import_subtitles(
    edit: EditableProject,
    subtitle_file: str,
    *,
    offset: int = 0,
    replace: bool = False,
    sequence: str | None = None,
) -> SubtitleImport:
    """Import an SRT or WebVTT file as the sequence's subtitles (written at save time)."""
    path, kind, cues, warnings = _read_cues(subtitle_file)
    seq = _sequence(edit, sequence)
    uuid = props(seq).get("kdenlive:uuid")
    if uuid and uuid != edit.doc.doc_property("uuid"):
        raise ProjectError(
            "unsupported_structure",
            "Subtitles can only be imported into the project's main sequence for now.",
            "Kdenlive names other sequences' subtitle files differently; not verified yet.",
        )
    rate = edit.doc.rate
    spans = _place(cues, rate, offset, path.name, warnings)
    layers = _layers([(start, end) for start, end, _ in spans])
    rows = [
        (layer, _ass_time(start, rate), _ass_time(end, rate), _ass_text(cue, warnings))
        for layer, (start, end, cue) in zip(layers, spans, strict=True)
    ]
    existing, replaced = _existing(edit, seq, replace)
    profile = edit.root.find("profile")
    width = int(profile.get("width", "1920")) if profile is not None else 1920
    height = int(profile.get("height", "1080")) if profile is not None else 1080
    document = _ass_document(rows, width, height, max(layers))
    edit.pending_files.append(
        PendingFile(".ass", document.encode("utf-8"), _pointer(edit, seq, existing))
    )
    edit.changes.append(f"subtitles: imported {len(rows)} from {path.name}")
    return SubtitleImport(
        source=str(path),
        format=kind,
        events=len(rows),
        layers=max(layers) + 1,
        first_frame=spans[0][0],
        last_frame=max(end for _, end, _ in spans),
        replaced_events=replaced,
        warnings=warnings,
    )
