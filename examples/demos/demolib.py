"""Shared helpers for the demos and the end-to-end tests.

- drive the real ``kdenlive-mcp`` server over stdio, like an assistant would;
- generate synthetic media with FFmpeg (colors, drawn text, tones, optional espeak-ng speech);
- check rendered files objectively (streams, duration, audio levels, pixel colors).

Every external program is started from an argument list, never through a shell.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

# Variables the server needs; everything else (tokens, credentials…) stays out of the child.
_PASS = ("PATH", "HOME", "USER", "LANG", "TZ", "TMPDIR", "XDG_RUNTIME_DIR", "XDG_DATA_DIRS")
_PASS_PREFIXES = ("LC_", "MLT_", "KDENLIVE_MCP_", "COVERAGE_")


class ToolFailed(RuntimeError):
    """A tool returned an MCP error result."""

    def __init__(self, tool: str, text: str) -> None:
        super().__init__(f"{tool}: {text}")
        self.tool = tool
        self.text = text
        match = re.search(r"\[(\w+)\]", text)
        self.code = match.group(1) if match else None


def server_env(allowed_roots: list[Path] | None = None, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _PASS or k.startswith(_PASS_PREFIXES)}
    if allowed_roots is not None:
        env["KDENLIVE_MCP_ALLOWED_ROOTS"] = ":".join(str(p) for p in allowed_roots)
    env.update(extra)
    return env


class Session:
    """A connected MCP client with helpers that raise on tool errors."""

    def __init__(self, client: Client) -> None:
        self.client = client
        self.calls: list[str] = []

    async def call(self, tool: str, **args: Any) -> dict[str, Any]:
        self.calls.append(tool)
        result = await self.client.call_tool(tool, {k: v for k, v in args.items()})
        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            raise ToolFailed(tool, text)
        return result.structured_content or {}

    async def timeline(self, path: Path, **args: Any) -> dict[str, Any]:
        return await self.call("get_timeline", path=str(path), **args)

    async def clip(self, path: Path, track: str, index: int = 0) -> tuple[dict[str, Any], str]:
        """The `index`-th clip of `track` and the revision to edit it with."""
        tl = await self.timeline(path)
        track_data = next(t for t in tl["tracks"] if t["label"] == track)
        return track_data["clips"][index], tl["file"]["sha256"]


@asynccontextmanager
async def connect(env: Mapping[str, str]) -> AsyncIterator[Session]:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_kdenlive"], env=dict(env)
    )
    async with Client(params) as client:
        yield Session(client)


# ------------------------------------------------------------------ media generation ----
def have(*programs: str) -> bool:
    return all(shutil.which(p) for p in programs)


def _exec(
    args: list[str], *, timeout: float = 300, text: bool = True
) -> subprocess.CompletedProcess[Any]:
    """Run a program found on PATH with an argument list (no shell), failing on errors."""
    program = shutil.which(args[0])
    if program is None:
        raise FileNotFoundError(f"{args[0]} is not on PATH")
    return subprocess.run(  # noqa: S603 - argument list, resolved executable, no shell
        [program, *args[1:]],
        check=True,
        capture_output=True,
        text=text,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def _run(args: list[str], timeout: float = 300) -> subprocess.CompletedProcess[str]:
    return _exec(args, timeout=timeout)


def ffmpeg(*args: str) -> None:
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args])


def _drawtext(text: str, size: int, color: str = "white", y: str = "(h-text_h)/2") -> str:
    safe = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")
    return f"drawtext=text='{safe}':fontsize={size}:fontcolor={color}:x=(w-text_w)/2:y={y}"


def _sine(hz: float, seconds: float, peak_db: float) -> str:
    """A sine whose peak is `peak_db` dBFS (its mean volume is about 3 dB lower)."""
    amplitude = 10 ** (peak_db / 20)
    return f"aevalsrc={amplitude:.6f}*sin(2*PI*{hz}*t):s=48000:d={seconds}"


def make_clip(
    path: Path,
    seconds: float,
    *,
    color: str = "red",
    label: str | None = None,
    size: str = "1280x720",
    fps: str = "30000/1001",
    tone_hz: float | None = 440.0,
    tone_db: float = -12.0,
) -> Path:
    """A solid-color video with an optional centered label and an optional sine tone."""
    vf = f"color=c={color}:s={size}:r={fps}:d={seconds}"
    if label:
        height = int(size.split("x")[1])
        vf += "," + _drawtext(label, max(24, height // 12))
    args = ["-f", "lavfi", "-i", vf]
    if tone_hz is not None:
        args += [
            "-f", "lavfi", "-i",
            _sine(tone_hz, seconds, tone_db),
            "-ac", "2", "-c:a", "aac", "-b:a", "128k",
        ]  # fmt: skip
    args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-t", str(seconds)]
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(*args, str(path))
    return path


def make_tone(path: Path, seconds: float, hz: float = 220.0, db: float = -12.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        "-f", "lavfi", "-i", _sine(hz, seconds, db), "-ac", "2", "-c:a", "pcm_s16le", str(path),
    )  # fmt: skip
    return path


def make_speech(path: Path, text: str, *, voice: str = "en") -> Path:
    """Speech from espeak-ng when available, otherwise a modulated tone of similar length."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if have("espeak-ng"):
        raw = path.with_suffix(".raw.wav")
        _run(["espeak-ng", "-v", voice, "-w", str(raw), "--", text])
        ffmpeg("-i", str(raw), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(path))
        raw.unlink()
        return path
    seconds = max(1.0, len(text.split()) * 0.4)
    return make_tone(path, seconds, hz=180.0)


# ------------------------------------------------------------------ objective checks ----
def probe(path: Path) -> dict[str, Any]:
    out = _run(
        ["ffprobe", "-v", "error", "-of", "json", "-show_streams", "-show_format", f"file:{path}"]
    ).stdout
    data: dict[str, Any] = json.loads(out)
    return data


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def stream_kinds(path: Path) -> list[str]:
    return [s["codec_type"] for s in probe(path)["streams"]]


def video_size(path: Path) -> tuple[int, int]:
    video = next(s for s in probe(path)["streams"] if s["codec_type"] == "video")
    return int(video["width"]), int(video["height"])


def mean_volume(path: Path, start: float, length: float) -> float:
    """Mean volume in dBFS of [start, start+length) (-91 means digital silence)."""
    proc = _exec(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-ss", str(start), "-t", str(length),
            "-i", f"file:{path}", "-vn", "-af", "volumedetect", "-f", "null", "-",
        ],
        timeout=120,
    )  # fmt: skip
    match = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", proc.stderr)
    if not match:
        return -91.0
    return -91.0 if match.group(1) == "-inf" else float(match.group(1))


def region_rgb(
    path: Path, t: float, box: tuple[float, float, float, float] = (0.4, 0.4, 0.2, 0.2)
) -> tuple[int, int, int]:
    """Average RGB of a region (x, y, w, h as fractions of the frame) at time t."""
    x, y, w, h = box
    crop = f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale=1:1:flags=area,format=rgb24"
    proc = _exec(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", str(t),
            "-i", f"file:{path}", "-frames:v", "1", "-vf", crop, "-f", "rawvideo", "-",
        ],
        timeout=120,
        text=False,
    )  # fmt: skip
    r, g, b = proc.stdout[:3]
    return r, g, b


def dominant(rgb: tuple[int, int, int]) -> str:
    """'red', 'green', 'blue', 'black', 'white' or 'mixed' for a solid-ish color."""
    r, g, b = rgb
    if max(rgb) < 40:
        return "black"
    if min(rgb) > 215:
        return "white"
    top = max(rgb)
    if top - sorted(rgb)[1] < 60:
        return "mixed"
    return {r: "red", g: "green", b: "blue"}[top]


def melt_render(project: Path, output: Path, *, timeout: float = 600) -> Path:
    """Render a project with melt directly (reference render, independent of the server)."""
    _run(
        [
            "melt", "-quiet", str(project), "-consumer", f"avformat:{output}",
            "vcodec=libx264", "acodec=aac", "preset=veryfast", "pix_fmt=yuv420p",
        ],
        timeout=timeout,
    )  # fmt: skip
    return output
