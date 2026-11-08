"""An assistant colours, crops and crossfades two clips (#14); the render is checked."""

from __future__ import annotations

import demolib as dl
import pytest

pytestmark = pytest.mark.anyio

FPS = 30000 / 1001


def runner(s: dl.Session, proj):
    async def run(tool: str, ref: str, rev: str, **args: object) -> dict:
        return await s.call(
            tool, path=str(proj), clip=ref, revision=rev, expected_sha256=rev,
            destination=str(proj), overwrite=True, **args,
        )  # fmt: skip

    return run


async def two_clips_with_a_crossfade(s: dl.Session, tmp_path, proj) -> None:
    """Red 0-90 (2 s of tail handle), blue 90-210 from 1 s into its source, mixed over 30."""
    red = dl.make_clip(tmp_path / "media" / "red.mp4", 5, color="red", tone_hz=440)
    blue = dl.make_clip(tmp_path / "media" / "blue.mp4", 5, color="blue", tone_hz=880)
    await s.call("create_project", media_path=str(red), destination=str(proj))
    edit = {"destination": str(proj), "overwrite": True}
    run = runner(s, proj)
    clip, rev = await s.clip(proj, "V1")
    await run("trim_clip", clip["ref"], rev, end=90)
    added = await s.call(
        "add_media_to_bin", path=str(proj), media_paths=[str(blue)],
        expected_sha256=(await s.timeline(proj))["file"]["sha256"], **edit,
    )  # fmt: skip
    await s.call(
        "insert_clip", path=str(proj), bin_id=added["media"][0]["bin_id"], track="V1",
        start=90, audio_track="A1", expected_sha256=added["saved"]["sha256"], **edit,
    )  # fmt: skip
    clip, rev = await s.clip(proj, "V1", 1)
    await run("trim_clip", clip["ref"], rev, start=120)
    clip, rev = await s.clip(proj, "V1", 1)
    await run("move_clip", clip["ref"], rev, start=90)
    clip, rev = await s.clip(proj, "V1", 1)
    assert (clip["start"], clip["end"]) == (90, 210)

    clip, rev = await s.clip(proj, "V1", 0)
    mixed = await run("add_mix", clip["ref"], rev, duration=30)
    spans = sorted((p["track"], p["start"], p["end"]) for p in mixed["timeline"]["clips"])
    assert spans == [("A1", 0, 105), ("A1", 75, 210), ("V1", 0, 105), ("V1", 75, 210)]


async def test_effects_and_crossfade_are_rendered(tmp_path, media_tools):
    proj = tmp_path / "fx.kdenlive"
    async with dl.connect(dl.server_env([tmp_path])) as s:
        run = runner(s, proj)
        catalog = await s.call("list_effects")
        ids = {e["id"]: e for e in catalog["effects"]}
        assert {"crop", "greyscale", "fade_to_black"} <= ids.keys()
        assert ids["greyscale"]["available"] is True

        await two_clips_with_a_crossfade(s, tmp_path, proj)

        red_v, rev = await s.clip(proj, "V1", 0)
        await run("add_effect", red_v["ref"], rev, effect="greyscale")
        red_v, rev = await s.clip(proj, "V1", 0)
        await run("add_effect", red_v["ref"], rev, effect="sepia")
        red_v, rev = await s.clip(proj, "V1", 0)
        removed = await run("remove_effect", red_v["ref"], rev, index=1)
        assert removed["effects"]["effects"] == ["greyscale"]

        blue_v, rev = await s.clip(proj, "V1", 1)
        await run("add_effect", blue_v["ref"], rev, effect="crop", params={"left": 200})
        blue_v, rev = await s.clip(proj, "V1", 1)
        updated = await run("update_effect", blue_v["ref"], rev, index=0, params={"left": 640})
        assert updated["effects"] == {
            "operation": "update", "clip": blue_v["ref"], "effects": ["crop"], "index": 0,
        }  # fmt: skip
        blue_v, rev = await s.clip(proj, "V1", 1)
        await run("add_effect", blue_v["ref"], rev, effect="fade_to_black", params={"duration": 30})

        tl = await s.timeline(proj)
        v1 = next(t for t in tl["tracks"] if t["label"] == "V1")
        assert [(m["start"], m["end"]) for m in v1["mixes"]] == [(75, 105)]
        assert v1["clips"][1]["effects"] == ["crop", "fade_to_black"]
        out = tmp_path / "out.mp4"
        await dl.render(s, proj, out)

    assert abs(dl.duration(out) - 210 / FPS) < 0.2
    grey = dl.region_rgb(out, 1.0)
    assert max(grey) - min(grey) < 20 and 40 < grey[0] < 200, grey  # greyscale red
    blend = dl.region_rgb(out, 90 / FPS)  # middle of the crossfade
    assert blend[2] > grey[2] + 20 and blend[2] < 200, (grey, blend)
    assert dl.dominant(dl.region_rgb(out, 5.0, (0.6, 0.4, 0.2, 0.2))) == "blue"
    assert dl.is_black(dl.region_rgb(out, 5.0, (0.05, 0.4, 0.2, 0.2)))  # cropped side
    assert dl.is_black(dl.region_rgb(out, 208 / FPS, (0.6, 0.4, 0.2, 0.2)))  # faded out
    assert dl.mean_volume(out, 85 / FPS, 10 / FPS) > -30  # audio crossfade, no gap


async def test_refusals_are_actionable_and_remove_mix_restores_the_cut(tmp_path, media_tools):
    proj = tmp_path / "fx.kdenlive"
    async with dl.connect(dl.server_env([tmp_path])) as s:
        run = runner(s, proj)
        await two_clips_with_a_crossfade(s, tmp_path, proj)
        red_v, rev = await s.clip(proj, "V1", 0)
        with pytest.raises(dl.ToolFailed) as unknown:
            await run("add_effect", red_v["ref"], rev, effect="blur")
        assert unknown.value.code == "invalid_argument" and "list_effects" in unknown.value.text
        with pytest.raises(dl.ToolFailed) as bad:
            await run("add_effect", red_v["ref"], rev, effect="crop", params={"top": -5})
        assert bad.value.code == "invalid_argument" and "between 0 and 10000" in bad.value.text
        with pytest.raises(dl.ToolFailed) as twice:
            await run("add_mix", red_v["ref"], rev)
        assert twice.value.code == "invalid_argument" and "remove_mix" in twice.value.text

        unmixed = await run("remove_mix", red_v["ref"], rev)
        spans = sorted((p["track"], p["start"], p["end"]) for p in unmixed["timeline"]["clips"])
        assert spans == [("A1", 0, 90), ("A1", 90, 210), ("V1", 0, 90), ("V1", 90, 210)]
        tl = await s.timeline(proj)
        assert all(t["mixes"] == [] for t in tl["tracks"])
        out = tmp_path / "out.mp4"
        await dl.render(s, proj, out)

    assert dl.dominant(dl.region_rgb(out, 88 / FPS)) == "red"  # hard cut again
    assert dl.dominant(dl.region_rgb(out, 92 / FPS)) == "blue"
