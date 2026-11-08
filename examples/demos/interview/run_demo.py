"""Interview: a deterministic MCP client edits a two-person interview, moves its media folder,
relinks it and renders. Usage: python run_demo.py WORKDIR.

Scenario: question (Sam, recorded too quietly) then answer (Ana); two silent cutaways over the
answer on a track above; lower-third titles naming each speaker; the transcript imported as
subtitles; both voices brought to the same level; guides at the question, the answer and the
cutaways; then the media folder is moved to an archive disk and relinked before the render.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import demolib as dl
import make_media

FPS = make_media.FPS


async def play(work: Path, media: dict[str, Path]) -> tuple[Path, list[str], dict]:
    project = work / "interview.kdenlive"
    out = work / "interview.mp4"
    failures: list[str] = []
    async with dl.connect(dl.server_env([work])) as s:
        await s.call(
            "create_project", media_path=str(media["question.mp4"]), destination=str(project)
        )
        ed = await dl.Editor(s, project).open()
        added = await ed.edit(
            "add_media_to_bin",
            media_paths=[str(media[n]) for n in ("answer.mp4", "cutaway_hull.mp4",
                                                 "cutaway_tools.mp4")],
        )  # fmt: skip
        ids = {Path(m["media"]).name: m["bin_id"] for m in added["media"]}
        q = (await ed.clips("V1"))[0]["end"]
        await ed.edit("insert_clip", bin_id=ids["answer.mp4"], track="V1", start=q)
        a = (await ed.clips("V1"))[1]["duration"]
        # Cutaways over the answer, on V2 (create_project makes two video tracks), without
        # their (silent) audio.
        hull, tools = q + FPS, q + 4 * FPS
        await ed.edit("insert_clip", bin_id=ids["cutaway_hull.mp4"], track="V2", start=hull,
                      source_out=2 * FPS - 1, audio_track=None)  # fmt: skip
        await ed.edit("insert_clip", bin_id=ids["cutaway_tools.mp4"], track="V2", start=tools,
                      source_out=2 * FPS - 1, audio_track=None)  # fmt: skip
        # Name titles on a new top track (V3), top left: the bottom is for the subtitles.
        await ed.edit("add_track", kind="video", name="Names")
        await ed.edit("add_title", text="Sam Lee\nInterviewer", track="V3", start=0,
                      duration=min(q, 3 * FPS), align="left", vertical="top", font_size=60,
                      outline=2)  # fmt: skip
        await ed.edit("add_title", text="Ana Ruiz\nBoat builder", track="V3", start=q,
                      duration=2 * FPS, align="left", vertical="top", font_size=60,
                      outline=2)  # fmt: skip
        # The transcript, bottom centre (Kdenlive's default subtitle style).
        imported = await ed.edit("import_subtitles", subtitle_file=str(media["interview.srt"]))
        if imported["subtitles"]["events"] != 3:
            failures.append(f"{imported['subtitles']['events']} subtitles imported, not 3")
        # Same level for both voices: the question was recorded 14 dB lower.
        await ed.on_clip("set_clip_volume", "A1", 0, level_db=14)
        # Guides for the editor.
        for position, comment in ((0, "Question"), (q, "Answer"), (hull, "Cutaway: hull"),
                                  (tools, "Cutaway: tools")):  # fmt: skip
            await ed.edit("add_marker", position=position, comment=comment)
        # The media folder goes to the archive disk: the project now misses it.
        archive = work / "archive" / "2026" / "media"
        archive.parent.mkdir(parents=True)
        shutil.move(work / "media", archive)
        report = await s.call("list_project_media", path=str(project))
        missing = [m for m in report["items"] if m["status"] == "missing"]
        if len(missing) != 4:
            failures.append(f"{len(missing)} missing media after the move, not 4")
        await ed.edit(
            "relink_media",
            mapping={m["reference"]: str(archive / Path(m["reference"]).name) for m in missing},
        )
        report = await s.call("list_project_media", path=str(project))
        if any(m["status"] != "ok" for m in report["items"] if m["kind"] == "file"):
            failures.append("media still missing after relinking")
        tl = await s.timeline(project)
        markers = await s.call("list_markers", path=str(project))
        if len(markers["guides"]) != 4:
            failures.append(f"{len(markers['guides'])} guides, not 4")
        await dl.render(s, project, out)
    return out, failures, {"q": q, "a": a, "hull": hull, "tools": tools, "timeline": tl}


def check(out: Path, at: dict) -> list[str]:
    failures = []
    q, a, hull, tools = at["q"], at["a"], at["hull"], at["tools"]
    expected = at["timeline"]["duration_frames"] / FPS
    if abs(dl.duration(out) - expected) > 0.2:
        failures.append(f"duration {dl.duration(out):.2f}s, expected {expected:.2f}s")
    side = (0.75, 0.1, 0.2, 0.15)  # away from the labels and the name titles
    for frame, colour, what in ((q // 2, "mixed", "question (grey)"),
                                (hull + FPS, "green", "hull cutaway"),
                                (tools + FPS, "blue", "tools cutaway")):  # fmt: skip
        got = dl.dominant(dl.region_rgb(out, frame / FPS, side))
        if got != colour:
            failures.append(f"{what} at {frame / FPS:.1f}s: {got}")
    top_left = (0.0, 0.0, 0.45, 0.3)
    if dl.bright_box(out, (q + FPS) / FPS, 200, top_left) is None:
        failures.append("no lower third for Ana")
    if dl.bright_box(out, (q + 3 * FPS) / FPS, 200, top_left) is not None:
        failures.append("Ana's lower third still visible after 2 s")
    bottom = (0.1, 0.75, 0.8, 0.25)
    if dl.bright_box(out, (q // 2) / FPS, 200, bottom) is None:
        failures.append("no subtitle under the question")
    question = dl.mean_volume(out, 0.5, q / FPS - 1.0)
    answer = dl.mean_volume(out, q / FPS + 0.5, a / FPS - 1.0)
    if abs(question - answer) > 4:
        failures.append(
            f"voices not harmonised: question {question:.1f} dB, answer {answer:.1f} dB"
        )
    return failures


def run(work: Path) -> tuple[Path, list[str]]:
    work.mkdir(parents=True, exist_ok=True)
    out, failures, at = anyio.run(play, work, make_media.make(work))
    return out, failures + check(out, at)


if __name__ == "__main__":
    out, failures = run(Path(sys.argv[1] if len(sys.argv) > 1 else "interview-work").resolve())
    print(out)
    for failure in failures:
        print("FAIL:", failure)
    print("OK" if not failures else f"{len(failures)} check(s) failed")
    raise SystemExit(1 if failures else 0)
