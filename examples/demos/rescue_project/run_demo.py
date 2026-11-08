"""Rescue of a Kdenlive-made project: a deterministic MCP client diagnoses, relinks, retouches
around Kdenlive's crossfades, then renders with melt. Usage: python run_demo.py WORKDIR.

The project (made by Kdenlive 26.08.2, two crossfades on V1 and A1) lost its media folder:
it was renamed from `media/` to `footage/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import demolib as dl
import make_media

FPS = 30000 / 1001


async def play(work: Path, files: dict[str, Path]) -> tuple[Path, list[str]]:
    project, footage = files["project"], files["footage"]
    out = work / "rescued.mp4"
    failures: list[str] = []
    async with dl.connect(dl.server_env([work])) as s:
        # 1. Diagnose: what is this project, what is missing?
        summary = await s.call("inspect_project", path=str(project))
        if "26.08" not in str(summary.get("kdenlive_version")):
            failures.append(f"unexpected Kdenlive version {summary.get('kdenlive_version')}")
        report = await s.call("list_project_media", path=str(project))
        missing = [m for m in report["items"] if m["status"] == "missing"]
        if {Path(m["reference"]).name for m in missing} != {
            "ntsc.mp4",
            "pal clip é.mp4",
            "tone.wav",
        }:
            failures.append(f"missing media not diagnosed: {[m['reference'] for m in missing]}")
        # 2. Relink every missing file to the renamed folder.
        ed = await dl.Editor(s, project).open()
        relinked = await ed.edit(
            "relink_media",
            mapping={m["reference"]: str(footage / Path(m["reference"]).name) for m in missing},
        )
        if len(relinked["relink"]["relinked"]) != 3:
            failures.append(f"relinked {len(relinked['relink']['relinked'])} of 3 files")
        report = await s.call("list_project_media", path=str(project))
        if any(m["status"] == "missing" for m in report["items"]):
            failures.append("media still missing after relinking")
        # 3. Retouch around Kdenlive's crossfades.
        tl = await s.timeline(project)
        v1 = next(t for t in tl["tracks"] if t["label"] == "V1")
        if [(m["start"], m["end"]) for m in v1["mixes"]] != [(30, 60), (70, 90)]:
            failures.append(f"unexpected crossfades {v1['mixes']}")
        try:  # the end of the first clip is inside a crossfade: refused, nothing written
            await ed.on_clip("trim_clip", "V1", 0, end=50)
            failures.append("trimming into a crossfade was accepted")
        except dl.ToolFailed as err:
            if err.code != "unsupported_structure":
                failures.append(f"trim into a crossfade: {err.code}")
        await ed.on_clip("remove_mix", "V1", 0)  # shorten the first crossfade: 30 -> 12
        await ed.on_clip("add_mix", "V1", 0, duration=12)
        await ed.on_clip("set_clip_volume", "A1", 1, level_db=-6)  # the middle clip is loud
        await ed.on_clip("add_effect", "V1", 2, effect="fade_to_black", params={"duration": 20})
        await ed.on_clip("set_clip_fades", "A1", 2, fade_out=20)
        await ed.edit("add_marker", position=0, comment="Opening")
        await ed.edit("add_marker", position=70, comment="Last shot", category=1)
        tl = await s.timeline(project)
        # 4. Render with melt (the engine for Kdenlive projects without Kdenlive's own renderer).
        result = await s.call("render_project", path=str(project), output=str(out), engine="melt")
        if result.get("engine") != "melt":
            failures.append(f"rendered with {result.get('engine')}")
    return out, failures + check(out, tl)


def check(out: Path, timeline: dict) -> list[str]:
    failures = []
    expected = timeline["duration_frames"] / FPS
    if abs(dl.duration(out) - expected) > 0.2:
        failures.append(f"duration {dl.duration(out):.2f}s, expected {expected:.2f}s")
    if dl.is_black(dl.region_rgb(out, 1.0)):
        failures.append("black picture in the middle: media not found by the render")
    if dl.mean_volume(out, 0.2, 0.6) < -50:
        failures.append("no sound at the start")
    end = dl.duration(out)
    if not dl.is_black(dl.region_rgb(out, end - 0.05)):
        failures.append("the last clip does not fade to black")
    if dl.mean_volume(out, end - 0.15, 0.12) > dl.mean_volume(out, 0.2, 0.6) - 6:
        failures.append("the sound does not fade out")
    v1 = next(t for t in timeline["tracks"] if t["label"] == "V1")
    if [(m["start"], m["end"]) for m in v1["mixes"]] != [(34, 46), (70, 90)]:
        failures.append(f"crossfades after the retouch: {v1['mixes']}")
    return failures


def run(work: Path) -> tuple[Path, list[str]]:
    return anyio.run(play, work, make_media.make(work))


if __name__ == "__main__":
    out, failures = run(Path(sys.argv[1] if len(sys.argv) > 1 else "rescue-work").resolve())
    print(out)
    for failure in failures:
        print("FAIL:", failure)
    print("OK" if not failures else f"{len(failures)} check(s) failed")
    raise SystemExit(1 if failures else 0)
