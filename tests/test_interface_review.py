"""Interface review before the 1.0 freeze (#63): stable refs, groups, no-op writes, backups."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from test_timeline_edit import Session

from mcp_kdenlive import edits, writer
from mcp_kdenlive import timeline_edit as te
from mcp_kdenlive.errors import ProjectError

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def s(tmp_path) -> Session:
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    return Session(tmp_path / "p")


def refs(s: Session) -> dict[str, list[str]]:
    return {t.label: [c.ref for c in t.clips] for t in s.timeline().tracks if t.clips}


def test_refs_name_the_track_and_start(s):
    assert refs(s) == {"A1": ["A1@0"], "V1": ["V1@0"]}
    s.apply(te.split_clip, "V1@0", s.rev(), 30)
    assert refs(s) == {"A1": ["A1@0", "A1@30"], "V1": ["V1@0", "V1@30"]}
    s.apply(te.move_clip, "V1@30", s.rev(), 90)
    # the untouched clip keeps its ref; the moved one is named by its new start
    assert refs(s) == {"A1": ["A1@0", "A1@90"], "V1": ["V1@0", "V1@90"]}


def test_secondary_layer_refs(s, tmp_path):
    s.current = s.ws / "kd26-mixes.kdenlive"
    assert refs(s)["V1"] == ["V1@0", "V1@30", "V1@70"]
    s.apply(te.trim_clip, "V1@70", s.rev(), end=120)


@pytest.mark.parametrize("bad", ["V9@0", "V1@5", "V1@x", "X1@0", "V1@0#2"])
def test_bad_refs(s, bad):
    with pytest.raises(ProjectError) as err:
        s.apply(te.remove_clip, bad, s.rev())
    assert err.value.code in {"clip_not_found", "invalid_argument"}


def test_legacy_refs_still_work(s):
    s.apply(te.remove_clip, "3:0:0", s.rev())
    assert "V1" not in refs(s)


def test_groups_are_shown(s):
    v1 = next(t for t in s.timeline().tracks if t.label == "V1")
    assert v1.clips[0].group == ["A1@0"]
    s.apply(te.insert_clip, "4", "V2", 0, 0, 9, None)
    v2 = next(t for t in s.timeline().tracks if t.label == "V2")
    assert v2.clips[0].group == []


def test_unchanged_project_is_not_rewritten(s):
    source = s.current
    before = source.read_bytes()
    edit = writer.open_for_edit(str(source))
    added = edits.add_media(edit, [str(s.ws / "media" / "ntsc.mp4")])
    assert added[0].status == "existing"
    saved = writer.save(edit, str(source), overwrite=True, expected_sha256=edit.sha256)
    assert saved.written is False and saved.backup is None
    assert source.read_bytes() == before
    assert not list(s.ws.glob("*.backup-*"))


def test_backups_are_rotated(s, monkeypatch):
    source = s.current
    for i in range(8):
        edit = writer.open_for_edit(str(source))
        edits.add_track(edit, "video", f"t{i}")
        writer.save(edit, str(source), overwrite=True, expected_sha256=edit.sha256)
    backups = sorted(s.ws.glob(f"{source.stem}.backup-*.kdenlive"))
    assert len(backups) == writer.KEEP_BACKUPS
