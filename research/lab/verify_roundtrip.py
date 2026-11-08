"""Open a .kdenlive file in real Kdenlive (headless lab), Save As a copy, and compare timelines.

Usage: uv run python research/lab/verify_roundtrip.py PROJECT.kdenlive [...]
Exit status 0 only if, for every project: Kdenlive shows no dialog while loading, the copy is
written, and the timeline read back from Kdenlive's copy equals the original (tracks, clip
positions, source in/out, resources). Requires the lab prerequisites (see README.md).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from mcp_kdenlive import kdenlive_doc, project

LAB = Path(__file__).resolve().parent


def clips(path: Path) -> list[tuple]:
    tl = project.timeline(kdenlive_doc.load(str(path)))
    return [(t.label, c.start, c.end, c.source_in, c.source_out, c.resource)
            for t in tl.tracks for c in t.clips]


def verify(path: Path) -> bool:
    with tempfile.TemporaryDirectory(prefix="kdenlive-verify-") as tmp:
        lab = Path(tmp)
        for sub in ("xdg/config", "xdg/data", "xdg/cache"):
            (lab / sub).mkdir(parents=True)
        copy = path.with_name(path.stem + ".kdenlive-resaved.kdenlive")
        script = lab / "script.txt"
        script.write_text(
            'click "Start Normally"\nsleep 5\ndialogs\n'
            "dbus /kdenlive/MainWindow_1 org.kde.KMainWindow.activateAction file_save_as\n"
            f"sleep 4\ndsettext {path.name} {copy}\ndclick Save\nsleep 5\ndialogs\n",
            encoding="utf-8",
        )
        out = lab / "out.txt"
        env = {"KD_ARGS": str(path), "KD_THEME": "kde"}
        import os

        subprocess.run([str(LAB / "kdgui.sh"), str(lab), str(script), str(out)],
                       env=dict(os.environ, **env), timeout=240, check=False)
        log = out.read_text(encoding="utf-8", errors="replace")
        first = log.split("dbus ", 1)[0]
        load_dialogs = [ln for ln in first.splitlines() if ln.startswith("WINDOW dialog")]
        ok = True
        if load_dialogs:
            print(f"  ✗ dialogs while loading: {load_dialogs}")
            ok = False
        if not copy.exists():
            print("  ✗ Kdenlive did not write the copy")
            print("    " + "\n    ".join(ln for ln in log.splitlines()
                                       if ln.startswith(("WINDOW", "DSETTEXT", "DCLICK", "CLICK", "DBUS", "app"))))
            return False
        try:
            same = clips(path) == clips(copy)
        finally:
            saved_version = kdenlive_doc.load(str(copy)).doc_property("kdenliveversion")
            copy.unlink()
        print(f"  {'✓' if same else '✗'} timeline identical after Kdenlive {saved_version} save")
        return ok and same


def main() -> int:
    results = []
    for arg in sys.argv[1:]:
        path = Path(arg).resolve()
        print(path.name)
        results.append(verify(path))
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
