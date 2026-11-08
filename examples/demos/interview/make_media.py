"""Synthetic media for the interview: a quiet question, a loud answer, two silent cutaways and
the subtitles of what is said (espeak-ng speech when installed, tones otherwise)."""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import demolib as dl

FPS = 25
QUESTION = "What do you make in this workshop?"
ANSWER = (
    "We build wooden boats, by hand. Each one takes about six months. The hull is the hardest part."
)


def talking(path: Path, text: str, *, color: str, label: str, gain_db: float) -> Path:
    """A shot of someone speaking: coloured picture, label, speech at a given level."""
    voice = dl.make_speech(path.with_suffix(".voice.wav"), text)
    seconds = math.ceil(dl.duration(voice) * FPS + 5) / FPS  # whole frames, a little air
    picture = dl.make_clip(
        path.with_suffix(".picture.mp4"), seconds, color=color, label=label, size="1920x1080",
        fps=str(FPS), tone_hz=None,
    )  # fmt: skip
    dl.ffmpeg(
        "-i", str(picture), "-i", str(voice), "-filter_complex",
        f"[1:a]volume={gain_db}dB,apad[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy",
        "-c:a", "aac", "-ac", "2", "-t", f"{seconds}", str(path),
    )  # fmt: skip
    voice.unlink()
    picture.unlink()
    return path


def srt_time(frame: int) -> str:
    ms = round(frame * 1000 / FPS)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def make(work: Path) -> dict[str, Path]:
    media = work / "media"
    media.mkdir(parents=True, exist_ok=True)
    out = {
        "question.mp4": talking(media / "question.mp4", QUESTION, color="dimgray",
                                label="SAM", gain_db=-14),
        "answer.mp4": talking(media / "answer.mp4", ANSWER, color="saddlebrown",
                              label="ANA", gain_db=0),
        "cutaway_hull.mp4": dl.make_clip(media / "cutaway_hull.mp4", 4, color="darkgreen",
                                         label="HULL", size="1920x1080", fps=str(FPS),
                                         tone_hz=None),
        "cutaway_tools.mp4": dl.make_clip(media / "cutaway_tools.mp4", 4, color="navy",
                                          label="TOOLS", size="1920x1080", fps=str(FPS),
                                          tone_hz=None),
    }  # fmt: skip
    q = round(dl.duration(out["question.mp4"]) * FPS)
    a = round(dl.duration(out["answer.mp4"]) * FPS)
    first, rest = ANSWER.split(". ", 1)
    cues = [(0, q - 5, QUESTION), (q, q + a // 2, first + "."), (q + a // 2, q + a - 5, rest)]
    srt = work / "interview.srt"
    srt.write_text(
        "".join(f"{i}\n{srt_time(s)} --> {srt_time(e)}\n{t}\n\n" for i, (s, e, t) in
                enumerate(cues, 1)),
        encoding="utf-8",
    )  # fmt: skip
    out["interview.srt"] = srt
    return out


if __name__ == "__main__":
    for path in make(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()).values():
        print(path)
