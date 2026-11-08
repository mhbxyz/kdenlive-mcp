"""Vertical short (9:16, 30 fps): a deterministic MCP client plays the scenario, then the render
is checked. Usage: python run_demo.py WORKDIR (media are generated there).

Scenario: three landscape shots cropped to fill a vertical frame, crossfaded, the last one
graded darker; a title over the opening; a music bed under everything, lowered and faded out.
"""

from __future__ import annotations

import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import demolib as dl
import make_media

FPS = 30
SHOT = 90  # frames kept from each 120-frame shot, leaving handles for the crossfades
MIX = 20


async def play(work: Path, media: dict[str, Path]) -> tuple[Path, list[str]]:
    project = work / "short.kdenlive"
    out = work / "short.mp4"
    async with dl.connect(dl.server_env([work])) as s:
        await s.call(
            "create_project", media_path=str(media["beach.mp4"]), destination=str(project),
            profile="vertical_hd_30",
        )  # fmt: skip
        ed = await dl.Editor(s, project).open()
        await ed.on_clip("trim_clip", "V1", 0, end=SHOT)  # beach: 0-90, source 0-89
        added = await ed.edit(
            "add_media_to_bin",
            media_paths=[str(media[n]) for n in ("city.mp4", "night.mp4", "music.wav")],
        )
        ids = {Path(m["media"]).name: m["bin_id"] for m in added["media"]}
        for i, name in enumerate(("city.mp4", "night.mp4"), 1):  # source 15-104: both handles
            await ed.edit(
                "insert_clip", bin_id=ids[name], track="V1", start=i * SHOT, source_in=15,
                source_out=15 + SHOT - 1, audio_track="A1",
            )  # fmt: skip
        for i in range(3):  # fill the 9:16 frame: crop the sides around the centre
            await ed.on_clip("add_effect", "V1", i, effect="crop", params={"center": True})
        await ed.on_clip("add_effect", "V1", 2, effect="gamma", params={"gamma": 0.6})
        await ed.on_clip("add_mix", "V1", 0, duration=MIX)
        await ed.on_clip("add_mix", "V1", 1, duration=MIX)
        await ed.edit("add_track", kind="video", name="Titles")
        await ed.edit(
            "add_title", text="Summer\nin 3 shots", track="V3", start=0, duration=2 * FPS,
            vertical="top", font_size=120, outline=3,
        )  # fmt: skip
        await ed.edit("insert_clip", bin_id=ids["music.wav"], track="A2", start=0)
        length = (await s.timeline(project))["duration_frames"]  # the mixes shortened it
        await ed.on_clip("trim_clip", "A2", 0, end=length)
        await ed.on_clip("set_clip_volume", "A2", 0, level_db=-8)
        await ed.on_clip("set_clip_fades", "A2", 0, fade_in=10, fade_out=45)
        tl = await s.timeline(project)
        await dl.render(s, project, out)
    return out, check(out, tl)


def check(out: Path, timeline: dict) -> list[str]:
    """Objective checks of the render; returns the failures (empty when all pass)."""
    failures = []
    width, height = dl.video_size(out)
    if (width, height) != (1080, 1920):
        failures.append(f"frame is {width}x{height}, not 1080x1920")
    expected = timeline["duration_frames"] / FPS
    if abs(dl.duration(out) - expected) > 0.2:
        failures.append(f"duration {dl.duration(out):.2f}s, expected {expected:.2f}s")
    if dl.is_black(dl.region_rgb(out, 1.0, (0.3, 0.02, 0.4, 0.06))):  # cropped, not letterboxed
        failures.append("top of the frame is black: the shot was letterboxed, not cropped")
    if dl.dominant(dl.region_rgb(out, 1.5, (0.3, 0.6, 0.4, 0.1))) != "red":  # darkorange
        failures.append("first shot not visible")
    top = (0.0, 0.0, 1.0, 0.3)  # the shots' own labels sit in the middle of the frame
    if dl.bright_box(out, 0.5, 200, top) is None:
        failures.append("no title in the top of the frame at 0.5s")
    if dl.bright_box(out, 3.0, 200, top) is not None:
        failures.append("title still visible after its 2 seconds")
    night = dl.region_rgb(out, dl.duration(out) - 1.2, (0.3, 0.6, 0.4, 0.1))
    city = dl.region_rgb(out, 5.0, (0.3, 0.6, 0.4, 0.1))
    if not (sum(night) < sum(city)):
        failures.append(f"graded night shot {night} is not darker than the city shot {city}")
    end = dl.duration(out)
    if dl.mean_volume(out, end - 0.3, 0.25) > dl.mean_volume(out, 4.0, 1.0) - 6:
        failures.append("music does not fade out at the end")
    return failures


def run(work: Path) -> tuple[Path, list[str]]:
    work.mkdir(parents=True, exist_ok=True)
    media = make_media.make(work)
    return anyio.run(play, work, media)


if __name__ == "__main__":
    out, failures = run(Path(sys.argv[1] if len(sys.argv) > 1 else "vertical-short-work").resolve())
    print(out)
    for failure in failures:
        print("FAIL:", failure)
    print("OK" if not failures else f"{len(failures)} check(s) failed")
    raise SystemExit(1 if failures else 0)
