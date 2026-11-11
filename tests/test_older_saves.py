"""Projects saved by Kdenlive 24.12.3 and 25.04.3 (#86).

Each fixture is a project of this suite opened in the official AppImage of that release and saved
with *Save As* (see tests/fixtures/PROVENANCE.md). The server must read it exactly like the
26.08.2 file it came from, and edit it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mcp_kdenlive import kdenlive_doc, markers, project, subtitles, writer
from mcp_kdenlive import timeline_edit as te

FIX = Path(__file__).parent / "fixtures"
RELEASES = ["24.12", "25.04"]

# resaved fixture -> the 26.08.2 (or server-written) file it was made from
SOURCES = {
    "kd26/kd{v}-mixes.kdenlive": "kd26/kd26-mixes.kdenlive",
    "kd26/kd{v}-subtitle.kdenlive": "kd26/kd26-subtitle.kdenlive",
    "kd26/kd{v}-montage.kdenlive": "kd26/generated-montage.kdenlive",
    "kd26/kd{v}-guides.kdenlive": "kd26/kd26-ntsc-timeline.kdenlive",
    "upstream/test-keyframes.resaved-{full}.kdenlive": (
        "upstream/test-keyframes.resaved-26.08.2.kdenlive"
    ),
}
FULL = {"24.12": "24.12.3", "25.04": "25.04.3"}
CASES = [
    (pattern.format(v=v, full=FULL[v]), source)
    for v in RELEASES
    for pattern, source in SOURCES.items()
]
ALL = [c[0] for c in CASES] + [f"kd26/kd{v}-title.kdenlive" for v in RELEASES]


def load(name: str) -> kdenlive_doc.KdenliveDocument:
    return kdenlive_doc.load(str((FIX / name).resolve()))


def reading(name: str) -> dict:
    """get_timeline's reading, without the file identity and the internal producer ids."""
    data = json.loads(project.timeline(load(name)).model_dump_json())
    data.pop("file")
    for track in data["tracks"]:
        for clip in track["clips"]:
            clip.pop("producer_id", None)
    return data


@pytest.mark.parametrize(("name", "source"), CASES, ids=[c[0] for c in CASES])
def test_resaved_project_reads_like_its_source(name, source):
    """Same tracks, clips, mixes, effects and guides; the guides file only adds its two guides."""
    got, want = reading(name), reading(source)
    if "guides" in name:
        got.pop("guides"), want.pop("guides")
    else:
        assert json.loads(markers.list_markers(load(name)).model_dump_json()) == json.loads(
            markers.list_markers(load(source)).model_dump_json()
        )
    assert got == want


@pytest.mark.parametrize("release", RELEASES)
def test_guides_added_in_older_kdenlive(release):
    guides = reading(f"kd26/kd{release}-guides.kdenlive")["guides"]
    assert [(g["position"], g["comment"], g["category"]) for g in guides] == [
        (10, "Intro é", 0),
        (45, "Chapter 2", 1),
    ]


@pytest.mark.parametrize("release", RELEASES)
def test_subtitles_saved_by_older_kdenlive(release):
    def events(name: str) -> list[tuple[int, int, str]]:
        report = subtitles.list_subtitles(load(name))
        return [(e.start, e.end, e.text) for t in report.tracks for e in t.events]

    assert events(f"kd26/kd{release}-subtitle.kdenlive") == events("kd26/kd26-subtitle.kdenlive")
    assert events(f"kd26/kd{release}-subtitle.kdenlive") == [(60, 210, "Bonjour é ✓")]


@pytest.mark.parametrize("release", RELEASES)
def test_title_saved_by_older_kdenlive(release):
    tl = reading(f"kd26/kd{release}-title.kdenlive")
    v2 = next(t for t in tl["tracks"] if t["label"] == "V2")
    assert [(c["start"], c["end"]) for c in v2["clips"]] == [(0, 150)]
    xml = (FIX / f"kd26/kd{release}-title.kdenlive").read_text(encoding="utf-8")
    assert "kdenlivetitle" in xml and "Titre 25.04 é" in xml


@pytest.mark.parametrize("name", ALL)
def test_older_saves_can_be_edited(name, tmp_path):
    folder = FIX / name.split("/")[0]
    shutil.copytree(folder, tmp_path / "p")
    src = tmp_path / "p" / Path(name).name
    before = project.timeline(kdenlive_doc.load(str(src)))
    v1 = next(t for t in before.tracks if t.label == "V1")
    first = v1.clips[0]
    edit = writer.open_for_edit(str(src))
    keyframed = "keyframes" in name  # splitting animated effects is refused (docs/tools.md)
    if keyframed:
        te.move_clip(edit, first.ref, before.file.sha256, first.start + 10)
    else:
        te.split_clip(edit, first.ref, before.file.sha256, (first.start + first.end) // 2)
    markers.add_marker(edit, 1, comment="server guide")
    writer.save(edit, str(tmp_path / "p" / "edited.kdenlive"))

    after = project.timeline(kdenlive_doc.load(str(tmp_path / "p" / "edited.kdenlive")))
    clips = next(t for t in after.tracks if t.label == "V1").clips
    if keyframed:
        assert clips[0].start == first.start + 10
    else:
        assert len(clips) == len(v1.clips) + 1
        assert after.duration_frames == before.duration_frames
    assert "server guide" in [g.comment for g in after.guides]
