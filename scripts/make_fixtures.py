"""Regenerate synthetic test fixtures (#19). Never uses personal media.

  uv run python scripts/make_fixtures.py media DIR     # synthetic media, FFmpeg lavfi only
  uv run python scripts/make_fixtures.py montage   # tests/fixtures/kd26/generated-montage.kdenlive
  uv run python scripts/make_fixtures.py mixes     # tests/fixtures/kd26/kd26-mixes.kdenlive (#61)

Media: bit-exact FFmpeg flags, no metadata; identical bytes are not guaranteed across FFmpeg
versions, so committed media are the reference and this command documents how they were made.
Montage: built with this package's own editing functions from the Kdenlive 26.08.2 fixture,
so it changes when the editing code changes (review the diff; ids and uuids are random).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KD26 = ROOT / "tests" / "fixtures" / "kd26"

COMMON = ["-hide_banner", "-loglevel", "error", "-y", "-fflags", "+bitexact", "-map_metadata", "-1"]
MEDIA = {
    "ntsc.mp4": [
        "-f",
        "lavfi",
        "-i",
        "smptebars=size=320x180:rate=30000/1001:duration=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:duration=2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
    ],
    "pal clip é.mp4": [
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=25:duration=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
    ],
    "tone.wav": ["-f", "lavfi", "-i", "sine=frequency=220:duration=3", "-c:a", "pcm_s16le"],
}


def make_media(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name, args in MEDIA.items():
        subprocess.run(["ffmpeg", *COMMON, *args, str(target / name)], check=True)
        print(f"wrote {target / name}")


def make_montage() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from mcp_kdenlive import edits, kdenlive_doc, project, writer
    from mcp_kdenlive import timeline_edit as te

    # Work next to the fixture so relative media paths stay "media/…"; step files are removed.
    steps: list[Path] = []
    start = KD26 / ".montage-step0.kdenlive"
    shutil.copy(KD26 / "kd26-ntsc-timeline.kdenlive", start)
    steps.append(start)
    current = start

    def step(fn, *args):  # type: ignore[no-untyped-def]
        nonlocal current
        edit = writer.open_for_edit(str(current))
        fn(edit, *args)
        nxt = KD26 / f".montage-step{len(steps)}.kdenlive"
        writer.save(edit, str(nxt))
        steps.append(nxt)
        current = nxt

    def rev() -> str:
        return project.timeline(kdenlive_doc.load(str(current))).file.sha256

    def ref(label: str, start: int) -> str:
        tl = project.timeline(kdenlive_doc.load(str(current)))
        return next(
            c.ref for t in tl.tracks if t.label == label for c in t.clips if c.start == start
        )

    try:
        step(edits.add_track, "video", "Overlay é")
        step(edits.add_track, "audio", "Music")
        step(te.insert_clip, "5", "V1", 90)  # AV clip after a 30-frame gap
        step(te.insert_clip, "6", "A3", 15, 0, 59)  # WAV sub-range on the new bottom track
        step(te.split_clip, ref("V1", 0), rev(), 30)  # razor: grouped audio split too
        step(te.move_clip, ref("V1", 30), rev(), 160)  # move the right half (audio follows)
        step(te.insert_clip, "5", "V2", 40, 10, 39, None)  # video-only sub-range on V2
        step(te.trim_clip, ref("V1", 90), rev(), 95, 140)
        out = KD26 / "generated-montage.kdenlive"
        # Like the Kdenlive-saved fixtures: no machine path in `root` (see PROVENANCE.md).
        text = current.read_text(encoding="utf-8").replace(
            f'root="{KD26}"', 'root="/nonexistent/kdenlive-mcp-fixture-root"'
        )
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    finally:
        for path in steps:
            path.unlink(missing_ok=True)


def make_mixes() -> None:
    """Three adjacent AV clips with handles (built by this package), then real Kdenlive 26.08.2
    adds the crossfades: Select All, Mix Clips (headless GUI lab, research/lab), Save As."""
    import os
    import tempfile

    sys.path.insert(0, str(ROOT / "src"))
    from mcp_kdenlive import timeline_edit as te
    from mcp_kdenlive import writer

    base = KD26 / ".mixes-base.kdenlive"
    saved = KD26 / ".mixes-kdenlive.kdenlive"
    edit = writer.open_for_edit(str(KD26 / "kd26-ntsc.kdenlive"))
    # ntsc.mp4 has 60 frames: leave handles so Kdenlive can overlap the clips.
    te.insert_clip(edit, "4", "V1", 0, 0, 39)
    te.insert_clip(edit, "4", "V1", 40, 10, 49)
    te.insert_clip(edit, "4", "V1", 80, 10, 59)
    writer.save(edit, str(base))
    action = "dbus /kdenlive/MainWindow_1 org.kde.KMainWindow.activateAction"
    try:
        with tempfile.TemporaryDirectory(prefix="kdenlive-mixes-") as tmp:
            lab = Path(tmp)
            for sub in ("xdg/config", "xdg/data", "xdg/cache"):
                (lab / sub).mkdir(parents=True)
            script = lab / "script.txt"
            script.write_text(
                f'click "Start Normally"\nsleep 5\ndialogs\n{action} select_all_tracks\nsleep 1\n'
                f"{action} mix_clip\nsleep 2\n{action} unselect_all_tracks\nsleep 1\n"
                f"{action} file_save_as\nsleep 4\ndsettext {base.name} {saved}\ndclick Save\n"
                "sleep 5\ndialogs\n",
                encoding="utf-8",
            )
            env = dict(os.environ, KD_ARGS=str(base), KD_THEME="kde")
            subprocess.run(
                [str(ROOT / "research" / "lab" / "kdgui.sh"), str(lab), str(script), str(lab / "o")],
                env=env,
                timeout=240,
                check=False,
            )
        if not saved.exists():
            raise SystemExit("Kdenlive did not save the mixed project (see research/lab).")
        import re

        text = saved.read_text(encoding="utf-8").replace(
            f'root="{KD26}"', 'root="/nonexistent/kdenlive-mcp-fixture-root"'
        )
        # No machine path in the fixture (PROVENANCE.md): the file dialog's last folder.
        text = re.sub(
            r'(<property name="kdenlive:docproperties.browserurl">)[^<]*(</property>)',
            r"\1/nonexistent/kdenlive-mcp-fixture-root\2",
            text,
        )
        out = KD26 / "kd26-mixes.kdenlive"
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    finally:
        base.unlink(missing_ok=True)
        saved.unlink(missing_ok=True)
        for backup in KD26.glob(".mixes-*"):
            backup.unlink()


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "media" and len(sys.argv) == 3:
        make_media(Path(sys.argv[2]))
    elif command == "montage":
        make_montage()
    elif command == "mixes":
        make_mixes()
    else:
        raise SystemExit(__doc__)
