"""An assistant finds missing media after a folder move and relinks them (#17)."""

from __future__ import annotations

import shutil
from pathlib import Path

import demolib as dl
import pytest

pytestmark = pytest.mark.anyio
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "kd26"


async def test_relink_after_moving_the_media_folder(tmp_path, media_tools):
    shutil.copytree(FIX, tmp_path / "p")
    proj = tmp_path / "p" / "kd26-ntsc-timeline.kdenlive"
    shutil.move(tmp_path / "p" / "media", tmp_path / "p" / "archive 2026")
    fixed = tmp_path / "p" / "relinked.kdenlive"
    async with dl.connect(dl.server_env([tmp_path])) as s:
        listing = await s.call("list_project_media", path=str(proj))
        missing = [m for m in listing["items"] if m["status"] == "missing"]
        assert {m["bin_id"] for m in missing} == {"4", "5", "6"}
        mapping = {
            m["resolved_path"]: str(tmp_path / "p" / "archive 2026" / Path(m["resolved_path"]).name)
            for m in missing
        }
        result = await s.call(
            "relink_media", path=str(proj), mapping=mapping, destination=str(fixed)
        )
        assert sorted(r["bin_id"] for r in result["relink"]["relinked"]) == ["4", "5", "6"]
        assert all(r["warnings"] == [] for r in result["relink"]["relinked"])
        after = await s.call("list_project_media", path=str(fixed))
        assert [m for m in after["items"] if m["status"] == "missing"] == []
        before_tl = await s.timeline(proj)
        after_tl = await s.timeline(fixed)
        assert [t["clips"] for t in after_tl["tracks"]] != []
        assert [
            [(c["start"], c["end"], c["source_in"], c["source_out"]) for c in t["clips"]]
            for t in after_tl["tracks"]
        ] == [
            [(c["start"], c["end"], c["source_in"], c["source_out"]) for c in t["clips"]]
            for t in before_tl["tracks"]
        ]
        out = tmp_path / "out.mp4"
        await dl.render(s, fixed, out)
    assert abs(dl.duration(out) - 60 / 29.97) < 0.2
    assert not dl.is_black(dl.region_rgb(out, 1.0, (0.0, 0.0, 1.0, 0.6)))
