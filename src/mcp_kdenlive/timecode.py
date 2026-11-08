"""Frame-accurate time handling for MLT/Kdenlive values.

All internal positions are integer frame counts at the project's rational frame rate.
MLT accepts several textual forms, all of which appear in Kdenlive projects:

* plain integers: a frame count (``"125"``)
* clock strings ``HH:MM:SS.mmm`` (what Kdenlive 26.08 writes for ``in``/``out``/blank lengths)
* SMPTE ``HH:MM:SS:FF`` (non-drop) or ``HH:MM:SS;FF`` (drop-frame notation)

Clock strings are converted by rounding ``seconds * fps`` to the nearest frame, which is
exact for the millisecond values Kdenlive writes at 25 fps and the nearest valid frame at
rational rates such as 30000/1001.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

_CLOCK = re.compile(r"^(?:(\d+):)?(\d+):(\d+(?:[.,]\d+)?)$")
_SMPTE = re.compile(r"^(\d+):(\d+):(\d+)([:;])(\d+)$")


class TimeFormatError(ValueError):
    """A time value could not be interpreted."""


@dataclass(frozen=True)
class FrameRate:
    num: int
    den: int

    def __post_init__(self) -> None:
        if self.num <= 0 or self.den <= 0:
            raise ValueError(f"invalid frame rate {self.num}/{self.den}")

    @property
    def fps(self) -> Fraction:
        return Fraction(self.num, self.den)

    def __str__(self) -> str:
        return f"{self.num}/{self.den}" if self.den != 1 else str(self.num)

    @property
    def nominal(self) -> int:
        """Integer frames per second used for SMPTE labels (30 for 30000/1001)."""
        return round(self.fps)


def _round_half_up(value: Fraction) -> int:
    return int((value + Fraction(1, 2)) // 1)


def parse_time(value: str, rate: FrameRate) -> int:
    """Convert an MLT time value to a frame count."""
    text = value.strip()
    if not text:
        raise TimeFormatError("empty time value")
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    smpte = _SMPTE.match(text)
    if smpte:
        h, m, s, sep, f = smpte.groups()
        nominal = rate.nominal
        frames = ((int(h) * 60 + int(m)) * 60 + int(s)) * nominal + int(f)
        if sep == ";" and nominal in (30, 60):
            drop = 2 * (nominal // 30)
            total_minutes = int(h) * 60 + int(m)
            frames -= drop * (total_minutes - total_minutes // 10)
        return frames
    clock = _CLOCK.match(text)
    if clock:
        h, m, s = clock.groups()
        seconds = Fraction(int(h or 0) * 3600 + int(m) * 60) + Fraction(s.replace(",", "."))
        return _round_half_up(seconds * rate.fps)
    raise TimeFormatError(f"unrecognised time value {value!r}")


def frames_to_seconds(frames: int, rate: FrameRate) -> float:
    return float(Fraction(frames) / rate.fps)


def frames_to_timecode(frames: int, rate: FrameRate) -> str:
    """Non-drop SMPTE-style label HH:MM:SS:FF using the nominal integer rate."""
    sign = "-" if frames < 0 else ""
    frames = abs(frames)
    nominal = rate.nominal
    seconds, ff = divmod(frames, nominal)
    minutes, ss = divmod(seconds, 60)
    hh, mm = divmod(minutes, 60)
    return f"{sign}{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def frames_to_clock(frames: int, rate: FrameRate) -> str:
    """Clock string HH:MM:SS.mmm as written by Kdenlive (milliseconds, rounded)."""
    millis = _round_half_up(Fraction(frames) / rate.fps * 1000)
    seconds, ms = divmod(millis, 1000)
    minutes, ss = divmod(seconds, 60)
    hh, mm = divmod(minutes, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}"
