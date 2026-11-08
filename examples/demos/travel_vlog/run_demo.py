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

FPS, SHOT, CHAPTER, VOICE_AT = (
    make_media.FPS,
    make_media.SHOT,
    make_media.CHAPTER,
    make_media.VOICE_AT,
)
MIX = 10


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
        if voices != make_media.voice_spans(media):
            failures.append(f"voice-overs at {voices}, not where the subtitles expect them")
        await ed.edit("import_subtitles", subtitle_file=str(media["voice-over.srt"]))
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
        # (top of the frame: the shots carry their own label in the middle)
        await ed.edit("add_title", text="Portugal\nSummer 2026", track="V2", start=0,
                      duration=2 * FPS, font_size=110, outline=3, vertical="top")  # fmt: skip
        await ed.edit("add_title", text="Thanks for watching", track="V2", start=length - 2 * FPS,
                      duration=2 * FPS, font_size=90, outline=3, vertical="top")  # fmt: skip
        # 5. Chapter guides.
        for c, (place, _, _) in enumerate(make_media.CHAPTERS):
            await ed.edit("add_marker", position=c * CHAPTER, comment=f"Day {c + 1}: {place}",
                          category=c)  # fmt: skip
        tl = await s.timeline(project)
        guides = (await s.call("list_markers", path=str(project)))["guides"]
        await dl.render(s, project, out)
    return out, failures, {"timeline": tl, "voices": voices, "guides": guides, "shots": len(shots)}


def check_timeline(at: dict) -> list[str]:
    """The project as the server reads it back."""
    failures: list[str] = []
    tl, voices, guides, shots = at["timeline"], at["voices"], at["guides"], at["shots"]
    music = next(t for t in tl["tracks"] if t["label"] == "A2")["clips"]
    for clip in music:  # ducked exactly under the voices
        under = any(a <= clip["start"] < b for a, b in voices)
        if clip["audio"]["level_db"] != (-20.0 if under else -8.0):
            failures.append(f"music {clip['start']}-{clip['end']}: {clip['audio']}")
    if music[0]["audio"]["fade_in"] == 0 or music[-1]["audio"]["fade_out"] != 2 * FPS:
        failures.append("music fades missing")
    v1 = next(t for t in tl["tracks"] if t["label"] == "V1")
    if len(v1["mixes"]) != shots - 1:
        failures.append(f"{len(v1['mixes'])} crossfades, not {shots - 1}")
    if [(g["position"], g["category"]) for g in guides] != [(c * CHAPTER, c) for c in range(3)]:
        failures.append(f"chapter guides {guides}")
    return failures


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
    top = (0.0, 0.0, 1.0, 0.3)  # titles; the shots' labels are in the middle
    if dl.bright_box(out, 1.0, 200, top) is None:
        failures.append("no intro title")
    if dl.bright_box(out, 4.0, 200, top) is not None:
        failures.append("intro title still visible at 4 s")
    if dl.bright_box(out, end - 1.0, 200, top) is None:
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
    return out, failures + check_timeline(at) + check(out, at)


if __name__ == "__main__":
    out, failures = run(Path(sys.argv[1] if len(sys.argv) > 1 else "vlog-work").resolve())
    print(out)
    for failure in failures:
        print("FAIL:", failure)
    print("OK" if not failures else f"{len(failures)} check(s) failed")
    raise SystemExit(1 if failures else 0)
