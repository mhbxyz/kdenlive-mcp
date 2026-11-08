"""An assistant builds a short montage through the real server, then the render is checked."""

from __future__ import annotations

import demolib as dl
import pytest

pytestmark = pytest.mark.anyio


async def test_cut_and_close_the_gap(tmp_path, media_tools):
    red = dl.make_clip(tmp_path / "media" / "red.mp4", 3, color="red", tone_db=-12)
    blue = dl.make_clip(tmp_path / "media" / "blue.mp4", 2, color="blue", tone_hz=None)
    proj = tmp_path / "montage.kdenlive"

    async with dl.connect(dl.server_env([tmp_path])) as s:
        created = await s.call("create_project", media_path=str(red), destination=str(proj))
        assert sorted(created["tracks"]) == ["A1", "V1"]
        sha = created["project_sha256"]
        edit = {"destination": str(proj), "overwrite": True}

        added = await s.call(
            "add_media_to_bin", path=str(proj), media_paths=[str(blue)], expected_sha256=sha, **edit
        )
        blue_id = added["media"][0]["bin_id"]
        sha = added["saved"]["sha256"]
        await s.call(
            "insert_clip", path=str(proj), bin_id=blue_id, track="V1", start=90,
            audio_track=None, expected_sha256=sha, **edit,
        )  # fmt: skip

        clip, rev = await s.clip(proj, "V1", 0)
        await s.call("split_clip", path=str(proj), clip=clip["ref"], revision=rev, at=45,
                     expected_sha256=rev, **edit)  # fmt: skip
        right, rev = await s.clip(proj, "V1", 1)
        assert right["start"] == 45
        await s.call("remove_clip", path=str(proj), clip=right["ref"], revision=rev,
                     expected_sha256=rev, **edit)  # fmt: skip
        blue_clip, rev = await s.clip(proj, "V1", 1)
        await s.call("move_clip", path=str(proj), clip=blue_clip["ref"], revision=rev, start=45,
                     expected_sha256=rev, **edit)  # fmt: skip

        tl = await s.timeline(proj)
        assert tl["duration_frames"] == 45 + 60
        audio = next(t for t in tl["tracks"] if t["label"] == "A1")
        assert [(c["start"], c["end"]) for c in audio["clips"]] == [(0, 45)]

        out = tmp_path / "out.mp4"
        await dl.render(s, proj, out)

    assert dl.stream_kinds(out).count("video") == 1
    assert abs(dl.duration(out) - 105 / 29.97) < 0.2
    assert dl.dominant(dl.region_rgb(out, 0.7)) == "red"
    assert dl.dominant(dl.region_rgb(out, 2.5)) == "blue"
    assert dl.mean_volume(out, 0.2, 1.0) > -25  # the tone of the red clip
    assert dl.mean_volume(out, 2.0, 1.0) < -60  # blue has no audio


def test_tool_failed_extracts_the_error_code():
    err = dl.ToolFailed(
        "render_project", "Error executing tool render_project: [unsupported_kdenlive] x"
    )
    assert err.code == "unsupported_kdenlive"
