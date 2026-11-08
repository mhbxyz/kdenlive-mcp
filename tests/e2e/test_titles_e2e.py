"""An assistant adds an opening title over the footage (#15); the render is measured."""

from __future__ import annotations

import demolib as dl
import pytest

pytestmark = pytest.mark.anyio

FPS = 30000 / 1001


async def test_centred_multiline_title_is_rendered(tmp_path, media_tools):
    clip = dl.make_clip(tmp_path / "media" / "red.mp4", 4, color="darkred", tone_hz=None)
    proj = tmp_path / "title.kdenlive"
    async with dl.connect(dl.server_env([tmp_path])) as s:
        created = await s.call("create_project", media_path=str(clip), destination=str(proj))
        edit = {"destination": str(proj), "overwrite": True}
        track = await s.call(
            "add_track", path=str(proj), kind="video",
            expected_sha256=created["project_sha256"], **edit,
        )  # fmt: skip
        r = await s.call(
            "add_title", path=str(proj), text="Été à Kyōto\nJour 1", track="V2", start=30,
            duration=60, font_size=90, outline=2, expected_sha256=track["saved"]["sha256"],
            **edit,
        )  # fmt: skip
        title = r["title"]
        assert title["clips"][0]["ref"] == "V2@30" and title["font_checked"] is True
        with pytest.raises(dl.ToolFailed) as missing:
            await s.call(
                "add_title", path=str(proj), text="x", track="V2", start=100,
                font="No Such Font Family", expected_sha256=r["saved"]["sha256"], **edit,
            )  # fmt: skip
        assert missing.value.code == "dependency_missing"
        tl = await s.timeline(proj)
        v2 = next(t for t in tl["tracks"] if t["label"] == "V2")
        assert [(c["start"], c["end"], c["service"]) for c in v2["clips"]] == [
            (30, 90, "kdenlivetitle")
        ]
        out = tmp_path / "out.mp4"
        await dl.render(s, proj, out)

    width, height = dl.video_size(out)
    assert dl.bright_box(out, 15 / FPS) is None  # before the title: dark red only
    box = dl.bright_box(out, 60 / FPS)
    assert box is not None, "no title in the render"
    left, top, right, bottom = box
    assert abs((left + right) / 2 - width / 2) < width * 0.03, box  # centred horizontally
    assert abs((top + bottom) / 2 - height / 2) < height * 0.08, box  # and vertically
    assert bottom - top > 90 * 1.2  # two lines
    assert dl.bright_box(out, 100 / FPS) is None  # after the title
    assert dl.dominant(dl.region_rgb(out, 60 / FPS, (0.02, 0.02, 0.1, 0.1))) == "red"
