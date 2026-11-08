"""An assistant edits a project whose crossfades were made by Kdenlive (#61), then renders it."""

from __future__ import annotations

import shutil
from pathlib import Path

import demolib as dl
import pytest

pytestmark = pytest.mark.anyio
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "kd26"


async def test_remove_the_middle_clip_of_a_crossfaded_sequence(tmp_path, media_tools):
    shutil.copytree(FIX, tmp_path / "p")
    proj = tmp_path / "p" / "kd26-mixes.kdenlive"
    edited = tmp_path / "p" / "edited.kdenlive"
    async with dl.connect(dl.server_env([tmp_path])) as s:
        tl = await s.timeline(proj)
        v1 = next(t for t in tl["tracks"] if t["label"] == "V1")
        assert [(m["start"], m["end"], m["cut"]) for m in v1["mixes"]] == [
            (30, 60, 40),
            (70, 90, 80),
        ]
        middle = next(c for c in v1["clips"] if c["layer"] == 1)
        result = await s.call(
            "remove_clip", path=str(proj), clip=middle["ref"], revision=tl["file"]["sha256"],
            destination=str(edited),
        )  # fmt: skip
        assert any("mix" in change for change in result["saved"]["changes"])
        after = await s.timeline(edited)
        v1 = next(t for t in after["tracks"] if t["label"] == "V1")
        assert [(c["start"], c["end"], c["layer"]) for c in v1["clips"]] == [
            (0, 40, 0),
            (80, 130, 0),
        ]
        assert v1["mixes"] == []
        out = tmp_path / "out.mp4"
        await dl.render(s, edited, out)

    assert abs(dl.duration(out) - 130 / 29.97) < 0.2
    assert not dl.is_black(dl.region_rgb(out, 0.5, (0.0, 0.0, 1.0, 0.6)))  # first clip
    assert dl.is_black(dl.region_rgb(out, 2.0, (0.0, 0.0, 1.0, 1.0)))  # the gap left behind
    assert not dl.is_black(dl.region_rgb(out, 3.5, (0.0, 0.0, 1.0, 0.6)))  # third clip
    assert dl.mean_volume(out, 0.2, 0.8) > -40
    assert dl.mean_volume(out, 1.6, 0.8) < -60


async def test_crossfaded_project_renders_without_gaps(tmp_path, media_tools):
    shutil.copytree(FIX, tmp_path / "p")
    proj = tmp_path / "p" / "kd26-mixes.kdenlive"
    out = tmp_path / "out.mp4"
    async with dl.connect(dl.server_env([tmp_path])) as s:
        await dl.render(s, proj, out)
    assert abs(dl.duration(out) - 130 / 29.97) < 0.2
    for t in (0.5, 1.5, 2.5, 3.8):  # inside clips and inside both crossfades
        assert not dl.is_black(dl.region_rgb(out, t, (0.0, 0.0, 1.0, 0.6)))
    assert dl.mean_volume(out, 0.2, 4.0) > -40
