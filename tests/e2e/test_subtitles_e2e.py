"""An assistant imports an SRT file (#16); the subtitles are read back and burnt into the render."""

from __future__ import annotations

import demolib as dl
import pytest

pytestmark = pytest.mark.anyio

FPS = 30000 / 1001
SRT = (
    "1\n00:00:00,500 --> 00:00:01,500\nBonjour à tous\nDeuxième ligne\n\n"
    "2\n00:00:02,500 --> 00:00:03,500\nFin\n"
)


async def test_imported_subtitles_are_listed_and_rendered(tmp_path, media_tools):
    clip = dl.make_clip(tmp_path / "media" / "c.mp4", 4, color="darkblue", tone_hz=None)
    srt = tmp_path / "talk.srt"
    srt.write_text(SRT, encoding="utf-8")
    proj = tmp_path / "subs.kdenlive"
    async with dl.connect(dl.server_env([tmp_path])) as s:
        created = await s.call("create_project", media_path=str(clip), destination=str(proj))
        edit = {"destination": str(proj), "overwrite": True}
        r = await s.call(
            "import_subtitles", path=str(proj), subtitle_file=str(srt),
            expected_sha256=created["project_sha256"], **edit,
        )  # fmt: skip
        assert r["subtitles"]["events"] == 2
        assert r["saved"]["side_files"] == [str(tmp_path / "subs.kdenlive.ass")]
        listed = await s.call("list_subtitles", path=str(proj))
        (track,) = listed["tracks"]
        assert track["status"] == "ok"
        assert [(e["start"], e["end"], e["text"]) for e in track["events"]] == [
            (15, 45, "Bonjour à tous\nDeuxième ligne"), (75, 105, "Fin"),
        ]  # fmt: skip
        with pytest.raises(dl.ToolFailed) as again:
            await s.call(
                "import_subtitles", path=str(proj), subtitle_file=str(srt),
                expected_sha256=r["saved"]["sha256"], **edit,
            )  # fmt: skip
        assert again.value.code == "subtitles_exist"
        out = tmp_path / "out.mp4"
        await dl.render(s, proj, out)

    height = dl.video_size(out)[1]
    during = dl.bright_box(out, 1.0, 180)
    assert during is not None, "no subtitle in the render"
    assert during[1] > height * 0.6  # in the lower part of the frame (ASS alignment 2)
    assert dl.bright_box(out, 2.0, 180) is None  # between the two subtitles
    assert dl.bright_box(out, 3.0, 180) is not None
