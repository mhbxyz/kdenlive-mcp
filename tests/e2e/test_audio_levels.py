"""An assistant lowers a clip's level and adds fades (#13); the rendered sound is measured."""

from __future__ import annotations

import demolib as dl
import pytest

pytestmark = pytest.mark.anyio


async def test_level_and_fades_are_audible(tmp_path, media_tools):
    clip = dl.make_clip(tmp_path / "media" / "tone.mp4", 4, color="green", tone_db=-6)
    proj = tmp_path / "level.kdenlive"
    async with dl.connect(dl.server_env([tmp_path])) as s:
        created = await s.call("create_project", media_path=str(clip), output=str(proj))
        sha = created["project_sha256"]
        a1, rev = await s.clip(proj, "A1")
        assert rev == sha
        edit = {"destination": str(proj), "overwrite": True, "expected_sha256": rev}
        r = await s.call(
            "set_clip_volume", path=str(proj), clip=a1["ref"], revision=rev, level_db=-12, **edit
        )
        assert r["audio"]["clips"][0]["level_db"] == -12
        a1, rev = await s.clip(proj, "A1")
        edit["expected_sha256"] = rev
        await s.call(
            "set_clip_fades", path=str(proj), clip=a1["ref"], revision=rev,
            fade_in=30, fade_out=30, **edit,
        )  # fmt: skip
        tl = await s.timeline(proj)
        a1 = next(t for t in tl["tracks"] if t["label"] == "A1")["clips"][0]
        assert a1["audio"] == {"level_db": -12.0, "fade_in": 30, "fade_out": 30}
        out = tmp_path / "out.mp4"
        await dl.render(s, proj, out)

    source = dl.mean_volume(clip, 1.5, 1.0)
    middle = dl.mean_volume(out, 1.5, 1.0)
    assert abs((source - middle) - 12) < 1.5, (source, middle)
    assert dl.mean_volume(out, 0.0, 0.25) < middle - 6  # fading in
    assert dl.mean_volume(out, 3.75, 0.25) < middle - 6  # fading out
