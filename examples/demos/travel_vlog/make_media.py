"""Synthetic media for the travel vlog: 12 shots in 3 chapters, 3 voice-overs, a music bed and
the subtitles of the voice-overs (FFmpeg; espeak-ng speech when installed, tones otherwise)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import demolib as dl

FPS = 25
CHAPTERS = [  # (place, colours of its four shots, voice-over)
    ("LISBON", ["red", "darkred", "orangered", "firebrick"],
     "Day one. Lisbon, its hills and its yellow trams."),
    ("PORTO", ["blue", "navy", "royalblue", "mediumblue"],
     "Day two. Porto and the river."),
    ("ALGARVE", ["green", "darkgreen", "forestgreen", "seagreen"],
     "Day three. The beaches of the Algarve."),
]  # fmt: skip


def make(work: Path) -> dict[str, Path]:
    media = work / "media"
    out: dict[str, Path] = {}
    for c, (place, colours, voice) in enumerate(CHAPTERS, 1):
        for s, colour in enumerate(colours, 1):
            name = f"{c:02d}-{place.lower()}-{s}.mp4"
            out[name] = dl.make_clip(media / name, 4, color=colour, label=f"{place} {s}",
                                     size="1920x1080", fps=str(FPS), tone_hz=None)  # fmt: skip
        out[f"voice-{c}.wav"] = dl.make_speech(media / f"voice-{c}.wav", voice)
    out["music.wav"] = dl.make_tone(media / "music.wav", 45, hz=196.0, db=-6)
    return out


if __name__ == "__main__":
    for path in make(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()).values():
        print(path)
