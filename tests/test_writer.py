"""Safe saving: round trip, conflicts, overwrite policy, path rebasing, open-project guard."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mcp_kdenlive import kdenlive_doc, media, project, writer
from mcp_kdenlive.kdenlive_doc import ProjectError

FIX = Path(__file__).parent / "fixtures"
KD26 = FIX / "kd26"


@pytest.fixture
def workspace(tmp_path) -> Path:
    shutil.copytree(KD26, tmp_path / "proj")
    return tmp_path / "proj"


def clips(path: Path) -> list[tuple]:
    tl = project.timeline(kdenlive_doc.load(str(path)))
    return [
        (t.label, c.start, c.end, c.source_in, c.source_out) for t in tl.tracks for c in t.clips
    ]


@pytest.mark.parametrize("name", ["kd26-ntsc-timeline", "kd26-ntsc"])
def test_noop_copy_is_byte_identical(workspace, name):
    src = workspace / f"{name}.kdenlive"
    result = writer.save(writer.open_for_edit(str(src)), str(workspace / "copy.kdenlive"))
    assert (workspace / "copy.kdenlive").read_bytes() == src.read_bytes()
    assert result.rebased_paths == [] and result.backup is None


@pytest.mark.parametrize(
    "path", sorted((FIX / "upstream").glob("*resaved-26.08.2.kdenlive")), ids=lambda p: p.name
)
def test_noop_copy_is_canonically_identical(tmp_path, path):
    shutil.copytree(path.parent, tmp_path / "u")
    src = tmp_path / "u" / path.name
    writer.save(writer.open_for_edit(str(src)), str(tmp_path / "u" / "copy.kdenlive"))
    import xml.etree.ElementTree as ET

    a = ET.canonicalize(src.read_text(encoding="utf-8"))
    b = ET.canonicalize((tmp_path / "u" / "copy.kdenlive").read_text(encoding="utf-8"))
    assert a == b


def test_copy_elsewhere_keeps_media_valid(workspace, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    result = writer.save(
        writer.open_for_edit(str(workspace / "kd26-ntsc-timeline.kdenlive")),
        str(other / "moved.kdenlive"),
    )
    assert any("media/ntsc.mp4 -> ../proj/media/ntsc.mp4" in r for r in result.rebased_paths)
    rep = media.media_report(kdenlive_doc.load(str(other / "moved.kdenlive")))
    assert rep.missing == [] and rep.counts["ok"] == 3
    assert rep.root.effective == str(other) and not rep.root.moved
    assert clips(other / "moved.kdenlive") == clips(workspace / "kd26-ntsc-timeline.kdenlive")


def test_far_destination_uses_absolute_paths(workspace, tmp_path):
    far = tmp_path / "a" / "b" / "c"
    far.mkdir(parents=True)
    writer.save(
        writer.open_for_edit(str(workspace / "kd26-ntsc.kdenlive")), str(far / "p.kdenlive")
    )
    resources = [
        i.reference
        for i in media.media_report(kdenlive_doc.load(str(far / "p.kdenlive"))).items
        if i.kind == "file"
    ]
    assert all(Path(r).is_absolute() for r in resources)


def test_unknown_metadata_survives(workspace):
    src = workspace / "kd26-ntsc-timeline.kdenlive"
    text = src.read_text(encoding="utf-8").replace(
        '<playlist id="main_bin">',
        '<!-- user note --><futuretag a="1"><x>y</x></futuretag><playlist id="main_bin">'
        '<property name="kdenlive:docproperties.future">keep me</property>',
        1,
    )
    src.write_text(text, encoding="utf-8")
    writer.save(writer.open_for_edit(str(src)), str(workspace / "copy.kdenlive"))
    copy = (workspace / "copy.kdenlive").read_text(encoding="utf-8")
    assert "<!-- user note -->" in copy and '<futuretag a="1"><x>y</x></futuretag>' in copy
    assert ">keep me<" in copy


def test_source_is_protected(workspace):
    src = workspace / "kd26-ntsc.kdenlive"
    edit = writer.open_for_edit(str(src))
    for kwargs in ({}, {"overwrite": True}, {"expected_sha256": edit.sha256}):
        with pytest.raises(ProjectError) as err:
            writer.save(writer.open_for_edit(str(src)), str(src), **kwargs)
        assert err.value.code == "source_protected"
    with pytest.raises(ProjectError) as err:
        writer.save(edit, str(src), overwrite=True, expected_sha256="0" * 64)
    assert err.value.code == "conflict"
    result = writer.save(
        writer.open_for_edit(str(src)), str(src), overwrite=True, expected_sha256=edit.sha256
    )
    assert result.backup and Path(result.backup).read_bytes() == edit.original


def test_conflict_when_source_changes(workspace):
    src = workspace / "kd26-ntsc.kdenlive"
    edit = writer.open_for_edit(str(src))
    src.write_bytes(src.read_bytes().replace(b"Sequence 1", b"Sequence X"))  # someone saved
    with pytest.raises(ProjectError) as err:
        writer.save(edit, str(workspace / "copy.kdenlive"))
    assert err.value.code == "conflict"
    assert not (workspace / "copy.kdenlive").exists()


def test_existing_destination_needs_overwrite_and_is_backed_up(workspace):
    dest = workspace / "dest.kdenlive"
    dest.write_text("old content")
    with pytest.raises(ProjectError) as err:
        writer.save(writer.open_for_edit(str(workspace / "kd26-ntsc.kdenlive")), str(dest))
    assert err.value.code == "output_exists" and dest.read_text() == "old content"
    result = writer.save(
        writer.open_for_edit(str(workspace / "kd26-ntsc.kdenlive")), str(dest), overwrite=True
    )
    assert Path(result.backup).read_text() == "old content"
    assert kdenlive_doc.load(str(dest)).generation == 5


@pytest.mark.parametrize(
    ("dest", "code"),
    [
        ("relative.kdenlive", "invalid_output"),
        ("{ws}/x.mlt", "invalid_output"),
        ("{ws}/nodir/x.kdenlive", "invalid_output"),
        ("{ws}/media", "invalid_output"),
    ],
)
def test_invalid_destinations(workspace, dest, code):
    with pytest.raises(ProjectError) as err:
        writer.save(
            writer.open_for_edit(str(workspace / "kd26-ntsc.kdenlive")), dest.format(ws=workspace)
        )
    assert err.value.code == code


def test_old_documents_are_not_edited():
    with pytest.raises(ProjectError) as err:
        writer.open_for_edit(str(FIX / "upstream" / "test-mix.kdenlive"))
    assert err.value.code == "unsupported_structure"


def test_failed_write_leaves_no_temporary_files(workspace, monkeypatch):
    def boom(*_a, **_k):
        raise ProjectError("internal_error", "simulated")

    monkeypatch.setattr(writer, "verify_serialization", boom)
    with pytest.raises(ProjectError):
        writer.save(
            writer.open_for_edit(str(workspace / "kd26-ntsc.kdenlive")),
            str(workspace / "copy.kdenlive"),
        )
    assert [
        p.name for p in workspace.iterdir() if p.name.startswith(".") or p.name == "copy.kdenlive"
    ] == []


def test_open_in_kdenlive_is_detected_and_blocks_overwrite(workspace, tmp_path):
    dest = workspace / "open.kdenlive"
    shutil.copy(workspace / "kd26-ntsc.kdenlive", dest)
    # A process whose argv[0] is "kdenlive" and whose arguments name the project, like a launch
    # of `kdenlive open.kdenlive` (the program is really a sleeping Python interpreter).
    proc = subprocess.Popen(
        ["kdenlive", "-c", "import time; time.sleep(30)", str(dest)], executable=sys.executable
    )
    try:
        time.sleep(0.2)
        usage = writer.kdenlive_usage(dest)
        assert usage.open is True and str(proc.pid) in usage.evidence[0]
        with pytest.raises(ProjectError) as err:
            writer.save(
                writer.open_for_edit(str(workspace / "kd26-ntsc.kdenlive")),
                str(dest),
                overwrite=True,
            )
        assert err.value.code == "open_in_kdenlive"
    finally:
        proc.kill()
        proc.wait()
    assert writer.kdenlive_usage(dest).open is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permissions")
def test_read_only_directory(workspace, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(ProjectError) as err:
            writer.save(
                writer.open_for_edit(str(workspace / "kd26-ntsc.kdenlive")),
                str(locked / "p.kdenlive"),
            )
        assert err.value.code == "invalid_output"
    finally:
        locked.chmod(0o700)
