"""Open a .kdenlive file in real Kdenlive (headless lab), Save As a copy, and compare timelines.

Usage: uv run python research/lab/verify_roundtrip.py PROJECT.kdenlive [...]
Exit status 0 only if, for every project: Kdenlive shows no dialog while loading, the copy is
written, and the timeline read back from Kdenlive's copy equals the original (tracks, clip
positions, source in/out, resources, groups, same-track mixes and clip layers). Requires the lab prerequisites (see README.md).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp_kdenlive import kdenlive_doc, project

LAB = Path(__file__).resolve().parent


def clips(path: Path) -> list[tuple]:
    """Tracks (label, kind, name), clips, and bin media references: what must survive."""
    doc = kdenlive_doc.load(str(path))
    tl = project.timeline(doc)
    tracks = [(t.label, t.kind, t.name) for t in tl.tracks]
    timeline = [(t.label, c.start, c.end, c.source_in, c.source_out, c.resource)
                for t in tl.tracks for c in t.clips]
    from mcp_kdenlive import media

    bin_media = sorted(i.resolved_path or "" for i in media.media_report(doc).items if i.kind == "file")
    # internal per-track transitions, by track label: wrong renumbering would show up here
    seq = next(t for t in doc.sequence_tractors()
               if kdenlive_doc.props(t).get("kdenlive:uuid") == tl.sequence_uuid)
    label_of = {t.index: t.label for t in tl.tracks}
    internal = sorted(
        (label_of.get(int(p.get("b_track", "-1"))), p.get("mlt_service"))
        for p in (kdenlive_doc.props(x) for x in seq.findall("transition"))
        if p.get("internal_added")
    )
    import json

    raw = kdenlive_doc.props(seq).get("kdenlive:sequenceproperties.groups", "").strip()
    groups = sorted(
        tuple(sorted(leaf["data"] for leaf in g.get("children", [])))
        for g in (json.loads(raw) if raw else [])
    )
    # same-track mixes (crossfades, #61): bounds, cut and service per track
    mixes = sorted((t.label, m.service, m.start, m.end, m.cut) for t in tl.tracks for m in t.mixes)
    layers = sorted((t.label, c.start, c.layer) for t in tl.tracks for c in t.clips)
    # clip effects (#13): identifiers in order, plus level and fade lengths read back
    effects = sorted(
        (t.label, c.start, tuple(c.effects), c.audio.model_dump_json() if c.audio else None)
        for t in tl.tracks
        for c in t.clips
    )
    # guides and clip markers (#12)
    from mcp_kdenlive import markers

    found = markers.list_markers(doc, tl.sequence_uuid)
    marks = sorted(
        (m.target, m.position, m.duration, m.comment, m.category)
        for m in found.guides + found.clip_markers
    )
    # effect parameters (#14): the stored value of every parameter the server writes
    from mcp_kdenlive.effects import SPECS

    def written(spec_id: str) -> list[str]:
        spec = SPECS[spec_id]
        return [*(p.name for p in spec.params if spec.edge is None), *(k for k, _ in spec.fixed)]

    def values(raw: str | None) -> tuple[str, ...] | None:
        # Kdenlive rewrites keyframe positions as timecodes ("0=1.5" -> "00:00:00.000=1.5",
        # "-1=1" -> "<end>=1"): compare the values, positions are checked by the bounds below
        return None if raw is None else tuple(k.rpartition("=")[2] for k in raw.split(";"))

    params = sorted(
        (e.get("producer", ""), e.get("in", ""), e.get("out", ""), pid,
         tuple((n, values(kdenlive_doc.props(f).get(n))) for n in written(pid)),
         (f.get("in") or e.get("in"), f.get("out")))
        for e in doc.root.iter("entry")
        for f in e.findall("filter")
        if (pid := kdenlive_doc.props(f).get("kdenlive_id", "")) in SPECS
    )
    return [tracks, timeline, bin_media, internal, groups, mixes, layers, effects, marks, params]


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
            before, after = clips(path), clips(copy)
            same = before == after
            for b, a in zip(before, after, strict=True):
                for item in sorted(set(map(repr, b)) ^ set(map(repr, a))):
                    print(f"    {'-' if item in map(repr, b) else '+'} {item}")
        finally:
            saved_version = kdenlive_doc.load(str(copy)).doc_property("kdenliveversion")
            if not os.environ.get("KD_KEEP"):
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
