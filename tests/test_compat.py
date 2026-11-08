"""Cross-version compatibility suite (#19): every fixture against tests/fixtures/compat.json."""

from __future__ import annotations

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mcp_kdenlive import kdenlive_doc, media, project, render, writer

FIX = Path(__file__).parent / "fixtures"
MANIFEST = json.loads((FIX / "compat.json").read_text(encoding="utf-8"))["fixtures"]
IDS = [entry["file"] for entry in MANIFEST]


def test_manifest_covers_every_fixture():
    on_disk = sorted(str(p.relative_to(FIX)) for p in FIX.glob("*/*.kdenlive"))
    assert sorted(IDS) == on_disk


@pytest.mark.parametrize("entry", MANIFEST, ids=IDS)
def test_reading_matches_manifest(entry):
    doc = kdenlive_doc.load(str((FIX / entry["file"]).resolve()))
    summary = project.summarize(doc)
    timeline = project.timeline(doc)
    observed = {
        "document_version": summary.document_version,
        "generation": summary.format_generation,
        "frame_rate": [summary.profile.frame_rate.num, summary.profile.frame_rate.den],
        "sequences": len(summary.sequences),
        "active_duration_frames": timeline.duration_frames,
        "active_clips": timeline.total_clips,
        "tracks": [t.label for t in timeline.tracks],
        "missing": len(media.media_report(doc).missing),
    }
    assert observed == {key: entry[key] for key in observed}


@pytest.mark.parametrize("entry", [e for e in MANIFEST if e["editable"]], ids=lambda e: e["file"])
def test_editable_fixtures_survive_a_noop_save(entry, tmp_path):
    src = (FIX / entry["file"]).resolve()
    dest = src.parent / f".compat-{src.stem}.kdenlive"
    try:
        writer.save(writer.open_for_edit(str(src)), str(dest))
        assert ET.canonicalize(src.read_text(encoding="utf-8")) == ET.canonicalize(
            dest.read_text(encoding="utf-8")
        )
    finally:
        dest.unlink(missing_ok=True)


def _renderable(entry: dict) -> bool:
    return (
        entry["generation"] >= 5 and entry["missing"] == 0 and entry["active_duration_frames"] > 0
    )


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("melt") is None or shutil.which("ffprobe") is None,
    reason="melt and ffprobe are required",
)
@pytest.mark.parametrize("entry", [e for e in MANIFEST if _renderable(e)], ids=lambda e: e["file"])
def test_melt_renders_every_complete_fixture(entry, tmp_path):
    out = tmp_path / "o.mkv"
    done = subprocess.run(
        [
            "melt",
            "-quiet",
            str(FIX / entry["file"]),
            "-consumer",
            f"avformat:{out}",
            "vcodec=ffv1",
            "acodec=pcm_s16le",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-500:]
    frames = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    ).stdout.strip()
    expected = entry["active_duration_frames"]
    # Kdenlive 26.08 stores a project tractor one frame longer than the last clip (docs/tools.md).
    assert int(frames) in (expected, expected + 1)


@pytest.mark.integration
@pytest.mark.parametrize(
    "entry",
    [e for e in MANIFEST if _renderable(e) and e["file"].startswith("kd26/")],
    ids=lambda e: e["file"],
)
def test_kdenlive_renders_kd26_fixtures(entry, tmp_path, request):
    if "subtitles_ass" in entry["features"]:
        request.applymarker(
            pytest.mark.xfail(
                reason="kdenlive --render 26.08.2 crashes (SIGSEGV) on projects with subtitles; "
                "reproduced outside this server (#16, #42)",
                strict=False,
            )
        )
    exe = shutil.which("kdenlive")
    if exe is None or shutil.which("ffprobe") is None or not render.supports_cli_render(exe):
        pytest.skip("Kdenlive with command-line rendering and ffprobe are required")
    result = render.render(
        render.prepare(str((FIX / entry["file"]).resolve()), str(tmp_path / "o.mp4")), timeout=300
    )
    assert result.state is render.RenderState.SUCCEEDED, (result.error, result.log_tail)
