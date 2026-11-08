"""Relinking missing media (#17) without losing timeline edits.

Kdenlive 26.08 keeps the media path in every copy of a clip: the bin clip and one `<chain>` per
timeline instance, each with `resource`, `kdenlive:file_hash` and `kdenlive:file_size`. Relinking
rewrites those, after checking that the new file can stand in for the old one (MLT reads it under
the project's profile, like add_media_to_bin).

melt is a stub here: the replacement's length and streams come from its name
(`…-30f.mp4` = 30 frames, `…-audio…` = audio only, `…-video…` = video only; default 60 frames AV).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from mlt_stubs import AUDIO_PRODUCER, AV_PRODUCER

from mcp_kdenlive import kdenlive_doc, media, project, relink, writer
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import props

FIX = Path(__file__).parent / "fixtures"
SED = shutil.which("sed")
VIDEO_PRODUCER = AV_PRODUCER.replace(
    '<property name="audio_index">1</property>', '<property name="audio_index">-1</property>'
)


@pytest.fixture
def ws(tmp_path, make_stub, stub_env, monkeypatch) -> Path:
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    shutil.copy(FIX / "profiles" / "atsc_720p_2997", profiles / "atsc_720p_2997")
    for name, text in (("av", AV_PRODUCER), ("audio", AUDIO_PRODUCER), ("video", VIDEO_PRODUCER)):
        (tmp_path / f"{name}.mlt").write_text(text)
    make_stub(
        "melt",
        f"""for last; do :; done
case "$4" in *-audio*) src="{tmp_path}/audio.mlt";; *-video*) src="{tmp_path}/video.mlt";;
  *) src="{tmp_path}/av.mlt";; esac
n=$(printf "%s" "$4" | {SED} -n 's/.*-\\([0-9]*\\)f\\..*/\\1/p'); [ -n "$n" ] || n=60
{SED} -e "s/>60</>$n</" -e "s/out=\\"59\\"/out=\\"$((n - 1))\\"/" "$src" > "${{last#xml:}}"
""",
    )
    for key, value in dict(stub_env, MLT_PROFILES_PATH=str(profiles)).items():
        monkeypatch.setenv(key, value)
    return tmp_path / "p"


def move_away(ws: Path, name: str = "ntsc.mp4") -> Path:
    """Make media/ntsc.mp4 missing; return a copy elsewhere to relink to."""
    target = ws / "moved" / name
    target.parent.mkdir(exist_ok=True)
    shutil.move(ws / "media" / "ntsc.mp4", target)
    return target


def load(path: Path):  # type: ignore[no-untyped-def]
    return kdenlive_doc.load(str(path))


def apply(ws: Path, mapping: dict[str, str], name: str = "relinked.kdenlive"):  # type: ignore[no-untyped-def]
    edit = writer.open_for_edit(str(ws / "kd26-ntsc-timeline.kdenlive"))
    result = relink.relink_media(edit, mapping)
    writer.save(edit, str(ws / name))
    return result, ws / name


def code_of(ws: Path, mapping: dict[str, str]) -> str:
    with pytest.raises(ProjectError) as err:
        apply(ws, mapping)
    return err.value.code


def chains_of(doc, bin_id: str) -> list[dict[str, str]]:  # type: ignore[no-untyped-def]
    return [
        props(el)
        for el in doc.root
        if el.tag in ("chain", "producer") and props(el).get("kdenlive:id") == bin_id
    ]


def clips(path: Path) -> list[tuple]:
    tl = project.timeline(load(path))
    return [
        (t.label, c.start, c.end, c.source_in, c.source_out) for t in tl.tracks for c in t.clips
    ]


@pytest.mark.parametrize("key", ["4", "media/ntsc.mp4", "ABS"])
def test_relink_every_copy_of_the_clip(ws, key):
    target = move_away(ws)
    before = clips(ws / "kd26-ntsc-timeline.kdenlive")
    key = str((ws / "media" / "ntsc.mp4").resolve()) if key == "ABS" else key
    result, out = apply(ws, {key: str(target)})
    assert [(r.bin_id, r.old_reference, r.new_reference) for r in result.relinked] == [
        ("4", "media/ntsc.mp4", "moved/ntsc.mp4")
    ]
    assert result.relinked[0].instances == 3  # the bin clip and its two timeline copies
    doc = load(out)
    expected_hash = "962afa417921dfa965a9be81585c582c"  # same bytes, moved
    for p in chains_of(doc, "4"):
        assert (p["resource"], p["kdenlive:file_hash"]) == ("moved/ntsc.mp4", expected_hash)
    assert clips(out) == before
    report = media.media_report(doc)
    assert report.missing == [] or all(m.bin_id != "4" for m in report.items if m.status != "ok")
    others = {p["resource"] for p in chains_of(doc, "5")}
    assert others == {"media/pal clip é.mp4"}


def test_absolute_reference_outside_the_project_folder(ws, tmp_path):
    outside = tmp_path / "elsewhere" / "Vidéo été.mp4"
    outside.parent.mkdir()
    shutil.copy(ws / "media" / "ntsc.mp4", outside)
    result, out = apply(ws, {"4": str(outside)})
    assert result.relinked[0].new_reference == str(outside)
    assert {p["resource"] for p in chains_of(load(out), "4")} == {str(outside)}


def test_shorter_replacement_that_still_covers_the_used_frames(ws):
    target = ws / "moved" / "short-60f.mp4"  # timeline uses 0..59 of a 60-frame clip
    target.parent.mkdir()
    shutil.copy(ws / "media" / "ntsc.mp4", target)
    result, _ = apply(ws, {"4": str(target)})
    assert result.relinked[0].warnings == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("clip-30f.mp4", "media_mismatch"),  # the timeline uses frames up to 59
        ("clip-audio.mp4", "media_mismatch"),  # the clip's video is on V1
        ("missing.mp4", "media_unreadable"),
    ],
)
def test_incompatible_replacements_are_refused(ws, name, expected):
    target = ws / "moved" / name
    target.parent.mkdir()
    if name != "missing.mp4":
        shutil.copy(ws / "media" / "ntsc.mp4", target)
    assert code_of(ws, {"4": str(target)}) == expected


def test_differences_that_do_not_break_the_edit_are_warnings(ws):
    target = ws / "moved" / "long-90f.mp4"
    target.parent.mkdir()
    shutil.copy(ws / "media" / "pal clip é.mp4", target)
    result, _ = apply(ws, {"4": str(target)})
    (warnings,) = [r.warnings for r in result.relinked]
    assert any("longer" in w for w in warnings)
    assert any("different file" in w for w in warnings)


def test_mapping_errors(ws, tmp_path):
    target = move_away(ws)
    assert code_of(ws, {"999": str(target)}) == "clip_not_found"
    assert code_of(ws, {"media/none.mp4": str(target)}) == "clip_not_found"
    assert code_of(ws, {"4": "relative/path.mp4"}) == "invalid_path"
    assert code_of(ws, {"4": "https://example.org/x.mp4"}) == "invalid_path"
    assert code_of(ws, {}) == "invalid_argument"
    assert code_of(ws, {"3": str(target)}) == "invalid_argument"  # the sequence


def test_one_file_used_by_two_bin_clips_needs_bin_ids(ws, tmp_path):
    src = ws / "kd26-ntsc-timeline.kdenlive"
    text = src.read_text(encoding="utf-8").replace("media/pal clip é.mp4", "media/ntsc.mp4")
    src.write_text(text, encoding="utf-8")
    target = move_away(ws)
    assert code_of(ws, {"media/ntsc.mp4": str(target)}) == "invalid_argument"
    result, _ = apply(ws, {"4": str(target), "5": str(target)})
    assert sorted(r.bin_id for r in result.relinked) == ["4", "5"]


def test_proxy_clips_are_refused(ws):
    src = ws / "kd26-ntsc-timeline.kdenlive"
    text = src.read_text(encoding="utf-8").replace(
        '<property name="kdenlive:id">4</property>',
        '<property name="kdenlive:id">4</property><property name="kdenlive:proxy">'
        "proxy/ntsc.mkv</property>",
    )
    src.write_text(text, encoding="utf-8")
    target = move_away(ws)
    assert code_of(ws, {"4": str(target)}) == "unsupported_structure"


def test_target_outside_allowed_roots(ws, tmp_path, monkeypatch):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.mp4"
    shutil.copy(ws / "media" / "ntsc.mp4", outside)
    try:
        monkeypatch.setenv("KDENLIVE_MCP_ALLOWED_ROOTS", str(ws))
        assert code_of(ws, {"4": str(outside)}) == "path_not_allowed"
    finally:
        os.unlink(outside)
