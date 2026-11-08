"""An assistant marks chapters with guides and a clip moment with a clip marker (#12)."""

from __future__ import annotations

import shutil
from pathlib import Path

import demolib as dl
import pytest

pytestmark = pytest.mark.anyio
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "kd26"


async def test_chapters_and_clip_markers(tmp_path):
    shutil.copytree(FIX, tmp_path / "p")
    proj = tmp_path / "p" / "kd26-ntsc-timeline.kdenlive"
    out = tmp_path / "p" / "marked.kdenlive"
    async with dl.connect(dl.server_env([tmp_path])) as s:
        listed = await s.call("list_markers", path=str(proj))
        assert listed["guides"] == [] and len(listed["categories"]) == 9
        await s.call(
            "add_marker", path=str(proj), position=0, comment="Intro", destination=str(out)
        )
        edit = {"destination": str(out), "overwrite": True}
        for position, comment in ((30, "Chapitre 1"), (45, "Chapitre 2 — été")):
            sha = (await s.timeline(out))["file"]["sha256"]
            await s.call(
                "add_marker", path=str(out), position=position, comment=comment, category=2,
                expected_sha256=sha, **edit,
            )  # fmt: skip
        sha = (await s.timeline(out))["file"]["sha256"]
        r = await s.call(
            "add_marker", path=str(out), position=12, comment="Clap", bin_id="4",
            expected_sha256=sha, **edit,
        )  # fmt: skip
        assert r["markers"]["marker"]["target"] == "4"
        assert r["markers"]["marker"]["clip_name"] == "ntsc.mp4"
        sha = (await s.timeline(out))["file"]["sha256"]
        with pytest.raises(dl.ToolFailed) as err:
            await s.call("add_marker", path=str(out), position=30, expected_sha256=sha, **edit)
        assert err.value.code == "invalid_argument"
        tl = await s.timeline(out)
        assert [(g["position"], g["comment"]) for g in tl["guides"]] == [
            (0, "Intro"),
            (30, "Chapitre 1"),
            (45, "Chapitre 2 — été"),
        ]
        listed = await s.call("list_markers", path=str(out))
        assert [(m["target"], m["position"]) for m in listed["clip_markers"]] == [("4", 12)]
