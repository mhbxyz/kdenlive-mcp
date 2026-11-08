"""Synthetic media for the vertical short: three landscape shots and a music bed (FFmpeg only)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import demolib as dl

SHOTS = {  # name: (colour, label, tone Hz) — 1920x1080 at 30 fps, 4 s each
    "beach.mp4": ("darkorange", "BEACH", 330.0),
    "city.mp4": ("royalblue", "CITY", 392.0),
    "night.mp4": ("purple", "NIGHT", 494.0),
}


def make(work: Path) -> dict[str, Path]:
    media = work / "media"
    out = {
        name: dl.make_clip(media / name, 4, color=color, label=label, size="1920x1080",
                           fps="30", tone_hz=hz, tone_db=-18)
        for name, (color, label, hz) in SHOTS.items()
    }  # fmt: skip
    out["music.wav"] = dl.make_tone(media / "music.wav", 12, hz=220.0, db=-10)
    return out


if __name__ == "__main__":
    for path in make(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()).values():
        print(path)
