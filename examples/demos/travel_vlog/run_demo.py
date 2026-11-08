"""Travel vlog: a deterministic MCP client edits a 12-shot, three-chapter vlog with voice-overs,
music, titles, chapter guides and subtitles, then renders it in 1080p.
Usage: python run_demo.py WORKDIR.
"""

from __future__ import annotations

import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import demolib as dl
import make_media

FPS = make_media.FPS
SHOT = 3 * FPS  # 3 s kept from each 4 s shot (source 12-86: handles for the crossfades)
MIX = 10
CHAPTER = 4 * SHOT
VOICE_AT = FPS  # each voice-over starts 1 s into its chapter


def srt_time(frame: int) -> str:
    ms = round(frame * 1000 / FPS)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


async def play(work: Path, media: dict[str, Path]) -> tuple[Path, list[str], dict]:
    project = work / "vlog.kdenlive"
    out = work / "vlog.mp4"
    failures: list[str] = []
    shots = sorted(n for n in media if n.endswith(".mp4"))
    async with dl.connect(dl.server_env([work])) as s:
        await s.call("create_project", media_path=str(media[shots[0]]), destination=str(project))
        ed = await dl.Editor(s, project).open()
        added = await ed.edit(
            "add_media_to_bin",
            media_paths=[str(media[n]) for n in [*shots[1:], "voice-1.wav", "voice-2.wav",
                                                  "voice-3.wav", "music.wav"]],
        )  # fmt: skip
        ids = {Path(m["media"]).name: m["bin_id"] for m in added["media"]}
        # 1. The 12 shots, 3 s each, crossfaded.
        await ed.on_clip("trim_clip", "V1", 0, end=SHOT)
        for i, name in enumerate(shots[1:], 1):
            await ed.edit("insert_clip", bin_id=ids[name], track="V1", start=i * SHOT,
                          source_in=12, source_out=12 + SHOT - 1, audio_track=None)  # fmt: skip
        for i in range(len(shots) - 1):
            await ed.on_clip("add_mix", "V1", i, duration=MIX)
        length = (await s.timeline(project))["duration_frames"]
        # 2. A voice-over per chapter on A1, and their subtitles.
        voices = []
        for c in range(3):
            start = c * CHAPTER + VOICE_AT
            placed = await ed.edit("insert_clip", bin_id=ids[f"voice-{c + 1}.wav"], track="A1",
                                   start=start)  # fmt: skip
            voices.append((start, placed["timeline"]["clips"][0]["end"]))
        srt = work / "voice-over.srt"
        srt.write_text("".join(
            f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{make_media.CHAPTERS[i - 1][2]}\n\n"
            for i, (a, b) in enumerate(voices, 1)), encoding="utf-8")  # fmt: skip
        await ed.edit("import_subtitles", subtitle_file=str(srt))
        # 3. Music on A2 for the whole vlog, ducked under each voice-over, faded in and out.
        await ed.edit("insert_clip", bin_id=ids["music.wav"], track="A2", start=0)
        await ed.on_clip("trim_clip", "A2", 0, end=length)
        for a, b in voices:
            for cut in (a, b):
                piece = next(i for i, c in enumerate(await ed.clips("A2")) if c["end"] > cut)
                await ed.on_clip("split_clip", "A2", piece, at=cut)
        for i, clip in enumerate(await ed.clips("A2")):
            under_voice = any(a <= clip["start"] < b for a, b in voices)
            await ed.on_clip("set_clip_volume", "A2", i, level_db=-20 if under_voice else -8)
        await ed.on_clip("set_clip_fades", "A2", 0, fade_in=FPS // 2)
        last = len(await ed.clips("A2")) - 1
        await ed.on_clip("set_clip_fades", "A2", last, fade_out=2 * FPS)
        # 4. Intro and outro titles on V2.
        await ed.edit("add_title", text="Portugal\nSummer 2026", track="V2", start=0,
                      duration=2 * FPS, font_size=110, outline=3)  # fmt: skip
        await ed.edit("add_title", text="Thanks for watching", track="V2", start=length - 2 * FPS,
                      duration=2 * FPS, font_size=90, outline=3)  # fmt: skip
        # 5. Chapter guides.
        for c, (place, _, _) in enumerate(make_media.CHAPTERS):
            await ed.edit("add_marker", position=c * CHAPTER, comment=f"Day {c + 1}: {place}",
                          category=c)  # fmt: skip
        tl = await s.timeline(project)
        audio = next(t for t in tl["tracks"] if t["label"] == "A2")["clips"]
        if sorted({c["audio"]["level_db"] for c in audio}) != [-20.0, -8.0]:
            failures.append(f"music levels {[c['audio'] for c in audio]}")
        if len((await s.call("list_markers", path=str(project)))["guides"]) != 3:
            failures.append("not 3 chapter guides")
        await dl.render(s, project, out)
    return out, failures, {"timeline": tl, "voices": voices}


def check(out: Path, at: dict) -> list[str]:
    failures = []
    tl, voices = at["timeline"], at["voices"]
    if dl.video_size(out) != (1920, 1080):
        failures.append(f"frame {dl.video_size(out)}, not 1920x1080")
    end = dl.duration(out)
    if abs(end - tl["duration_frames"] / FPS) > 0.2:
        failures.append(f"duration {end:.2f}s, expected {tl['duration_frames'] / FPS:.2f}s")
    side = (0.8, 0.1, 0.15, 0.15)
    for c, colour in enumerate(("red", "blue", "green")):  # one shot in the middle of each chapter
        t = (c * CHAPTER + 2.5 * SHOT) / FPS
        if dl.dominant(dl.region_rgb(out, t, side)) != colour:
            failures.append(f"chapter {c + 1} not {colour} at {t:.1f}s")
    middle = (0.2, 0.3, 0.6, 0.4)
    if dl.bright_box(out, 1.0, 200, middle) is None:
        failures.append("no intro title")
    if dl.bright_box(out, end - 1.0, 200, middle) is None:
        failures.append("no outro title")
    bottom = (0.1, 0.8, 0.8, 0.2)
    a, b = voices[1]
    if dl.bright_box(out, (a + b) / 2 / FPS, 200, bottom) is None:
        failures.append("no subtitle during the second voice-over")
    if dl.bright_box(out, (b + 10) / FPS, 200, bottom) is not None:
        failures.append("subtitle still shown after the voice-over")
    if dl.mean_volume(out, end - 0.3, 0.25) > dl.mean_volume(out, (b + 5) / FPS, 1.0) - 6:
        failures.append("music does not fade out")
    return failures


def run(work: Path) -> tuple[Path, list[str]]:
    work.mkdir(parents=True, exist_ok=True)
    out, failures, at = anyio.run(play, work, make_media.make(work))
    return out, failures + check(out, at)


if __name__ == "__main__":
    out, failures = run(Path(sys.argv[1] if len(sys.argv) > 1 else "vlog-work").resolve())
    print(out)
    for failure in failures:
        print("FAIL:", failure)
    print("OK" if not failures else f"{len(failures)} check(s) failed")
    raise SystemExit(1 if failures else 0)
