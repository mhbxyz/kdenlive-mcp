"""Two video tracks are composited in the render (#90: MLT's Qt module, qtblend)."""

from __future__ import annotations

import demolib as dl
import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("engine", ["auto", "melt"])
async def test_upper_track_shows_the_lower_one_where_it_is_cropped(tmp_path, media_tools, engine):
    red = dl.make_clip(tmp_path / "media" / "red.mp4", 3, color="red", tone_hz=None)
    blue = dl.make_clip(tmp_path / "media" / "blue.mp4", 3, color="blue", tone_hz=None)
    proj = tmp_path / "layers.kdenlive"
    async with dl.connect(dl.server_env([tmp_path])) as s:
        sha = (await s.call("create_project", media_path=str(red), destination=str(proj)))[
            "project_sha256"
        ]
        edit = {"destination": str(proj), "overwrite": True}
        sha = (
            await s.call("add_track", path=str(proj), kind="video", expected_sha256=sha, **edit)
        )["saved"]["sha256"]
        added = await s.call(
            "add_media_to_bin", path=str(proj), media_paths=[str(blue)], expected_sha256=sha, **edit
        )
        await s.call(
            "insert_clip", path=str(proj), bin_id=added["media"][0]["bin_id"], track="V2",
            start=0, audio_track=None, expected_sha256=added["saved"]["sha256"], **edit,
        )  # fmt: skip
        clip, rev = await s.clip(proj, "V2")
        await s.call(
            "add_effect", path=str(proj), clip=clip["ref"], revision=rev, effect="crop",
            params={"left": 640}, expected_sha256=rev, **edit,
        )  # fmt: skip
        out = tmp_path / "out.mp4"
        try:
            await s.call("render_project", path=str(proj), output=str(out), engine=engine)
        except dl.ToolFailed as err:  # Kdenlive 23.08 has no --render: auto uses melt anyway
            if err.code != "unsupported_kdenlive":
                raise
            pytest.skip("no kdenlive --render")

    # the crop keeps 640 px of V2, centred in the frame; V1 must show on both sides
    assert dl.dominant(dl.region_rgb(out, 1.5, (0.4, 0.4, 0.2, 0.2))) == "blue"
    assert dl.dominant(dl.region_rgb(out, 1.5, (0.02, 0.4, 0.15, 0.2))) == "red"
    assert dl.dominant(dl.region_rgb(out, 1.5, (0.83, 0.4, 0.15, 0.2))) == "red"
