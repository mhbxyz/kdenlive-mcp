"""Score a finished demo run against its brief: objective checks only.

The checks read the project back through the server (as `real_client.facts` does) and measure the
render, like the deterministic demo scripts, but they derive positions from the project the client
made instead of assuming the script's layout. Each check is pass/fail with a note.

    python examples/orchestration/score.py DEMO WORKDIR [--answer FILE]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demos"))
import demolib as dl

Check = tuple[str, bool, str]


def cues(srt: Path) -> int:
    return len(re.findall(r"-->", srt.read_text(encoding="utf-8"))) if srt.is_file() else -1


async def read(work: Path, project: Path) -> dict[str, Any]:
    async with dl.connect(dl.server_env([work])) as s:
        return {
            "timeline": await s.timeline(project),
            "media": await s.call("list_project_media", path=str(project)),
            "markers": await s.call("list_markers", path=str(project)),
            "subtitles": await s.call("list_subtitles", path=str(project)),
        }


def clips(tl: dict, kind: str | None = None) -> list[dict]:
    """All clips of video ('V') or audio ('A') tracks, with their track label."""
    found = []
    for track in tl["tracks"]:
        if kind and not track["label"].startswith(kind):
            continue
        for clip in track["clips"]:
            found.append({**clip, "track": track["label"]})
    return sorted(found, key=lambda c: (c["start"], c["track"]))


def named(items: list[dict], pattern: str) -> list[dict]:
    return [c for c in items if re.search(pattern, c.get("name") or "", re.I)]


def mixes(tl: dict, kind: str = "V") -> list[tuple[int, int]]:
    return [
        (m["start"], m["end"])
        for t in tl["tracks"]
        if t["label"].startswith(kind)
        for m in t["mixes"]
    ]


def render_basics(out: Path, tl: dict, fps: float, size: tuple[int, int]) -> list[Check]:
    if not out.is_file():
        return [("render", False, f"{out.name} missing")]
    got = dl.video_size(out)
    kinds = dl.stream_kinds(out)
    checks = [
        (
            "render",
            got == size and "video" in kinds and "audio" in kinds,
            f"{got[0]}x{got[1]} {kinds}",
        )
    ]
    expected = tl["duration_frames"] / fps
    checks.append(
        (
            "render duration",
            abs(dl.duration(out) - expected) <= 0.25,
            f"{dl.duration(out):.2f}s vs project {expected:.2f}s",
        )
    )
    return checks


def no_missing(info: dict) -> Check:
    missing = [m["reference"] for m in info["media"]["items"] if m["status"] == "missing"]
    return ("no missing media", not missing, ", ".join(missing[:3]) or "ok")


def near(value: float, target: float, tolerance: float) -> bool:
    return abs(value - target) <= tolerance


# ---------------------------------------------------------------------------- demos ----
def travel_vlog(work: Path, answer: str) -> list[Check]:
    fps = 25
    project, out = work / "vlog.kdenlive", work / "vlog.mp4"
    if not project.is_file():
        return [("project", False, "vlog.kdenlive missing")]
    info = anyio.run(read, work, project)
    tl = info["timeline"]
    video, audio = clips(tl, "V"), clips(tl, "A")
    checks = render_basics(out, tl, fps, (1920, 1080))
    shots = named(video, r"^0\d-.*\.mp4$")
    order = [c["name"] for c in shots]
    expected = sorted({c["name"] for c in shots})
    checks.append(
        (
            "12 shots in order",
            len(shots) == 12 and order == expected and len(expected) == 12,
            f"{len(shots)} shots",
        )
    )
    lengths = [c["end"] - c["start"] for c in shots]
    checks.append(
        (
            "shots ~3 s",
            bool(lengths) and all(2.4 * fps <= n <= 4 * fps for n in lengths),
            f"{min(lengths, default=0)}-{max(lengths, default=0)} frames",
        )
    )
    checks.append(
        ("11 crossfades", len(mixes(tl, "V")) >= 11, f"{len(mixes(tl, 'V'))} video mixes")
    )
    voices = [named(audio, rf"voice-{k}\.wav") for k in (1, 2, 3)]
    starts = [shots[4 * k]["start"] if len(shots) > 4 * k else None for k in range(3)]
    voice_ok = all(
        v and s is not None and near(v[0]["start"], s + fps, 8)
        for v, s in zip(voices, starts, strict=True)
    )
    checks.append(
        (
            "voice-overs 1 s into each day",
            voice_ok,
            str([(v[0]["start"] if v else None, s) for v, s in zip(voices, starts, strict=True)]),
        )
    )
    events = sum(len(t["events"]) for t in info["subtitles"]["tracks"])
    checks.append(
        ("subtitles imported", events == cues(work / "voice-over.srt"), f"{events} events")
    )
    music = named(audio, r"music")
    levels = sorted(
        {(c.get("audio") or {}).get("level_db") for c in music}, key=lambda x: (x is None, x)
    )
    quiet = bool(music) and all(lv is not None and lv <= -6 for lv in levels)
    checks.append(("music quiet", quiet, f"levels {levels}"))
    checks.append(
        (
            "music ducked under voices",
            len([lv for lv in levels if lv is not None]) >= 2,
            "two or more levels on the music",
        )
    )
    fades = (
        bool(music)
        and (music[0].get("audio") or {}).get("fade_in", 0) > 0
        and near((music[-1].get("audio") or {}).get("fade_out", 0), 2 * fps, 10)
    )
    checks.append(
        (
            "music fade in / 2 s fade out",
            fades,
            str([(c.get("audio") or {}) for c in (music[:1] + music[-1:])]),
        )
    )
    titles = [c["name"] for c in video if re.search(r"portugal|thanks", c.get("name") or "", re.I)]
    checks.append(
        (
            "intro and outro titles",
            any("ortugal" in t for t in titles) and any("hank" in t for t in titles),
            str(titles),
        )
    )
    guides = [g["position"] for g in info["markers"]["guides"]]
    day_ok = all(s is not None and any(near(g, s, 15) for g in guides) for s in starts)
    checks.append(("guide at each day", day_ok, f"guides {guides}, days {starts}"))
    checks.append(no_missing(info))
    if out.is_file():
        end = dl.duration(out)
        # No render check for the intro title: the brief does not say where it goes, and a
        # centred title lands on the test shots' own centred label.
        checks.append(
            (
                "render: music fades out",
                dl.mean_volume(out, end - 0.3, 0.25) < dl.mean_volume(out, end / 2, 1.0) - 6,
                "",
            )
        )
    return checks


def interview(work: Path, answer: str) -> list[Check]:
    fps = 25
    project, out = work / "interview.kdenlive", work / "interview.mp4"
    if not project.is_file():
        return [("project", False, "interview.kdenlive missing")]
    info = anyio.run(read, work, project)
    tl = info["timeline"]
    video, audio = clips(tl, "V"), clips(tl, "A")
    checks = render_basics(
        out,
        tl,
        fps,
        dl.video_size(work / "archive/2026/media/question.mp4")
        if (work / "archive/2026/media/question.mp4").is_file()
        else (1920, 1080),
    )
    q, a = named(video, r"question"), named(video, r"answer")
    checks.append(
        (
            "question then answer",
            bool(q and a) and q[0]["start"] < a[0]["start"],
            f"q {q[0]['start'] if q else None}, a {a[0]['start'] if a else None}",
        )
    )
    a0 = a[0]["start"] if a else 0
    hull, tools = named(video, r"hull"), named(video, r"tools")

    def above(c: dict) -> bool:  # V2 is above V1 (get_timeline lists tracks bottom-up)
        return bool(a) and int(c["track"][1:]) > int(a[0]["track"][1:])

    hull_ok = (
        bool(hull)
        and near(hull[0]["start"], a0 + fps, 8)
        and near(hull[0]["end"] - hull[0]["start"], 2 * fps, 8)
        and above(hull[0])
    )
    tools_ok = (
        bool(tools)
        and near(tools[0]["start"], a0 + 4 * fps, 8)
        and near(tools[0]["end"] - tools[0]["start"], 2 * fps, 8)
        and above(tools[0])
    )
    checks.append(
        (
            "cutaways at +1 s and +4 s, 2 s, over the picture",
            hull_ok and tools_ok,
            str([(c["start"], c["end"], c["track"]) for c in hull[:1] + tools[:1]]),
        )
    )
    voice = named(audio, r"answer")
    span = (a0 + fps, a0 + 6 * fps)
    checks.append(
        (
            "Ana's voice kept under the cutaways",
            any(c["start"] <= span[0] and c["end"] >= span[1] for c in voice)
            or (bool(voice) and voice[0]["start"] <= span[0] and voice[-1]["end"] >= span[1]),
            str([(c["start"], c["end"]) for c in voice]),
        )
    )
    titles = [
        c["name"]
        for c in video
        if re.search(r"sam|ana", c.get("name") or "", re.I)
        and not re.search(r"\.mp4$", c.get("name") or "")
    ]
    checks.append(
        (
            "two lower thirds",
            any("Sam" in t for t in titles) and any("Ana" in t for t in titles),
            str(titles),
        )
    )
    events = sum(len(t["events"]) for t in info["subtitles"]["tracks"])
    checks.append(
        ("transcript as subtitles", events == cues(work / "interview.srt"), f"{events} events")
    )
    guides = [g["position"] for g in info["markers"]["guides"]]
    checks.append(("4 guides", len(guides) >= 4, f"{guides}"))
    checks.append(no_missing(info))
    moved = [m["resolved_path"] or "" for m in info["media"]["items"] if m.get("reference")]
    checks.append(
        (
            "relinked to archive/2026/media",
            bool(moved) and all("archive/2026/media" in p for p in moved),
            f"{len(moved)} media",
        )
    )
    if out.is_file() and q and a:
        qv = dl.mean_volume(
            out, (q[0]["start"]) / fps + 0.5, max(0.5, (q[0]["end"] - q[0]["start"]) / fps - 1)
        )
        av = dl.mean_volume(out, a0 / fps + 0.5, max(0.5, (a[0]["end"] - a0) / fps - 1))
        checks.append(
            ("voices about as loud (±4 dB)", abs(qv - av) <= 4, f"q {qv:.1f} dB, a {av:.1f} dB")
        )
    return checks


def rescue_project(work: Path, answer: str) -> list[Check]:
    fps = 30000 / 1001
    project, out = work / "rescue.kdenlive", work / "rescued.mp4"
    original = work / ".orchestration-original.kdenlive"
    info = anyio.run(read, work, project)
    before = anyio.run(read, work, original) if original.is_file() else None
    tl = info["timeline"]
    checks = render_basics(out, tl, fps, dl.video_size(out) if out.is_file() else (0, 0))
    checks.append(("version named (26.08)", "26.08" in answer, ""))
    checks.append(no_missing(info))
    v1 = next((t for t in tl["tracks"] if t["label"] == "V1"), {"mixes": [], "clips": []})
    ms = [(m["start"], m["end"]) for m in v1["mixes"]]
    first_ok = len(ms) == 2 and 10 <= ms[0][1] - ms[0][0] <= 14
    second_ok = (
        len(ms) == 2
        and before is not None
        and ms[1]
        == next(
            (
                [(m["start"], m["end"]) for m in t["mixes"]][1]
                for t in before["timeline"]["tracks"]
                if t["label"] == "V1"
            ),
            None,
        )
    )
    checks.append(("first crossfade ~12 frames, second kept", first_ok and second_ok, str(ms)))
    vclips = v1["clips"]
    middle = vclips[len(vclips) // 2] if vclips else None
    a_clips = clips(tl, "A")
    mid_audio = [c for c in a_clips if middle and c["start"] == middle["start"]]
    level = (mid_audio[0].get("audio") or {}).get("level_db") if mid_audio else None
    checks.append(("middle shot -6 dB", level is not None and near(level, -6, 0.6), f"{level}"))
    guides = {g["comment"]: g["position"] for g in info["markers"]["guides"]}
    last = vclips[-1]["start"] if vclips else None
    checks.append(
        (
            "guides Opening / Last shot",
            "Opening" in guides
            and guides["Opening"] == 0
            and "Last shot" in guides
            and last is not None
            and near(guides["Last shot"], last, 2),
            str(guides),
        )
    )
    if before is not None:
        b_v1 = next(t for t in before["timeline"]["tracks"] if t["label"] == "V1")
        checks.append(
            (
                "nothing else touched (same clips on V1)",
                len(b_v1["clips"]) == len(vclips)
                and [t["label"] for t in before["timeline"]["tracks"]]
                == [t["label"] for t in tl["tracks"]],
                f"{len(b_v1['clips'])} → {len(vclips)} clips",
            )
        )
    if out.is_file():
        end = dl.duration(out)
        checks.append(("render: picture not black", not dl.is_black(dl.region_rgb(out, 1.0)), ""))
        checks.append(("render: fades to black", dl.is_black(dl.region_rgb(out, end - 0.05)), ""))
        checks.append(
            (
                "render: sound fades out",
                dl.mean_volume(out, end - 0.15, 0.12) < dl.mean_volume(out, 0.2, 0.6) - 6,
                "",
            )
        )
    return checks


def vertical_short(work: Path, answer: str) -> list[Check]:
    fps = 30
    project, out = work / "short.kdenlive", work / "short.mp4"
    if not project.is_file():
        return [("project", False, "short.kdenlive missing")]
    info = anyio.run(read, work, project)
    tl = info["timeline"]
    video, audio = clips(tl, "V"), clips(tl, "A")
    checks = render_basics(out, tl, fps, (1080, 1920))
    shots = named(video, r"(beach|city|night)\.mp4")
    order = [re.sub(r"\.mp4$", "", c["name"]) for c in shots]
    lengths = [c["end"] - c["start"] for c in shots]
    checks.append(
        (
            "three shots in order, ~3 s",
            order == ["beach", "city", "night"] and all(2.4 * fps <= n <= 4 * fps for n in lengths),
            f"{order} {lengths}",
        )
    )
    checks.append(("2 crossfades", len(mixes(tl, "V")) >= 2, f"{len(mixes(tl, 'V'))}"))
    titles = [c for c in video if re.search(r"summer", c.get("name") or "", re.I)]
    checks.append(
        (
            "title clip ~2 s at the start",
            bool(titles)
            and titles[0]["start"] <= 2
            and near(titles[0]["end"] - titles[0]["start"], 2 * fps, 8),
            str([(c["start"], c["end"]) for c in titles]),
        )
    )
    music = named(audio, r"music")
    mlev = [(c.get("audio") or {}).get("level_db") for c in music]
    checks.append(
        (
            "music quieter, fading in",
            bool(music)
            and all(lv is not None and lv < 0 for lv in mlev)
            and (music[0].get("audio") or {}).get("fade_in", 0) > 0,
            f"{mlev}",
        )
    )
    checks.append(no_missing(info))
    if out.is_file():
        end = dl.duration(out)
        checks.append(
            (
                "render: cropped, no black bars",
                not dl.is_black(dl.region_rgb(out, 1.0, (0.3, 0.02, 0.4, 0.06))),
                "",
            )
        )
        checks.append(
            (
                "render: title at the top, gone after 2 s",
                dl.bright_box(out, 0.5, 200, (0, 0, 1, 0.3)) is not None
                and dl.bright_box(out, 3.0, 200, (0, 0, 1, 0.3)) is None,
                "",
            )
        )
        night = dl.region_rgb(out, end - 0.5, (0.3, 0.6, 0.4, 0.1))
        city = dl.region_rgb(out, 4.5, (0.3, 0.6, 0.4, 0.1))
        checks.append(
            (
                "render: night darker than city",
                not dl.is_black(night) and sum(night) < sum(city),
                f"night {night} city {city}",
            )
        )
        checks.append(
            (
                "render: sound fades out",
                dl.mean_volume(out, end - 0.3, 0.25) < dl.mean_volume(out, 4.0, 1.0) - 6,
                "",
            )
        )
    return checks


DEMOS = {f.__name__: f for f in (travel_vlog, interview, rescue_project, vertical_short)}


def score(demo: str, work: Path, answer: str = "") -> dict[str, Any]:
    try:
        checks = DEMOS[demo](work, answer)
    except Exception as exc:  # a broken project is a failed run, not a crash of the bench
        checks = [("project readable", False, f"{type(exc).__name__}: {exc}"[:300])]
    passed = sum(ok for _, ok, _ in checks)
    return {
        "passed": passed,
        "total": len(checks),
        "checks": [{"check": n, "ok": ok, "note": note} for n, ok, note in checks],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("demo", choices=sorted(DEMOS))
    parser.add_argument("work", type=Path)
    parser.add_argument("--answer", type=Path)
    opts = parser.parse_args()
    text = opts.answer.read_text(encoding="utf-8") if opts.answer else ""
    print(json.dumps(score(opts.demo, opts.work.resolve(), text), indent=1))
