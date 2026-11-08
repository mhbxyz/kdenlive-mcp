"""Media resolution, missing-asset diagnostics and ffprobe parsing."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from mcp_kdenlive import kdenlive_doc, media
from mcp_kdenlive.kdenlive_doc import ProjectError
from mcp_kdenlive.media import MediaStatus

FIX = Path(__file__).parent / "fixtures"
KD26 = FIX / "kd26"
NTSC_REF = "media/ntsc.mp4"


def report(path: Path, **kw) -> media.MediaReport:
    return media.media_report(kdenlive_doc.load(str(path.resolve())), **kw)


def by_name(rep: media.MediaReport, name: str) -> media.MediaItem:
    return next(i for i in rep.items if name in (i.name, i.reference))


@pytest.fixture
def project_copy(tmp_path):
    """Copy the 26.08.2 project and its media to tmp; return a function writing variants."""
    shutil.copytree(KD26 / "media", tmp_path / "media")
    text = (KD26 / "kd26-ntsc-timeline.kdenlive").read_text(encoding="utf-8")

    def variant(
        resource: str = NTSC_REF,
        root: str | None = "/nonexistent/old-root",
        name: str = "p.kdenlive",
    ) -> Path:
        body = text.replace(f'name="resource">{NTSC_REF}<', f'name="resource">{resource}<')
        old_root = 'root="/nonexistent/kdenlive-mcp-fixture-root"'
        body = body.replace(old_root, f'root="{root}"' if root is not None else "")
        out = tmp_path / name
        out.write_text(body, encoding="utf-8")
        return out

    return variant


# ------------------------------------------------------------ real Kdenlive files ------
def test_moved_project_resolves_relative_to_project_dir():
    rep = report(KD26 / "kd26-ntsc-timeline.kdenlive")
    assert rep.root.moved is True
    assert rep.root.effective == str((KD26).resolve())
    item = by_name(rep, "ntsc.mp4")
    assert item.status is MediaStatus.OK
    assert item.resolved_path == str((KD26 / "media" / "ntsc.mp4").resolve())
    assert item.used_in_timeline is True
    assert by_name(rep, "tone.wav").used_in_timeline is False
    assert rep.counts == {"generated": 1, "ok": 3}  # the sequence clip is "generated"
    assert rep.missing == []


def test_unicode_and_spaces():
    item = by_name(report(KD26 / "kd26-ntsc.kdenlive"), "pal clip é.mp4")
    assert item.status is MediaStatus.OK
    assert item.resolved_path.endswith("media/pal clip é.mp4")


def test_missing_effect_asset_like_kdenlive():
    rep = report(FIX / "upstream" / "test-missing.kdenlive")
    (lut,) = rep.asset_files
    assert (lut.service, lut.property, lut.status) == (
        "avfilter.lut3d",
        "av.file",
        MediaStatus.MISSING,
    )
    assert rep.missing == ["/usr/share2/kdenlive/luts/GREEN_TINT.cube"]


def test_missing_proxy_with_available_original():
    rep = report(FIX / "upstream" / "missing-proxy.kdenlive")
    (clip,) = [i for i in rep.items if i.kind == "file"]
    assert clip.status is MediaStatus.OK
    assert clip.proxy_status is MediaStatus.MISSING
    assert clip.original_status is MediaStatus.OK
    assert clip.resolved_path.endswith("upstream/red.mpg")
    assert rep.missing == []


def test_generated_clips_need_no_file():
    rep = report(FIX / "upstream" / "test-nesting.kdenlive")
    assert {i.status for i in rep.items} == {MediaStatus.GENERATED}


# ------------------------------------------------------------- resolution rules -------
def test_existing_root_is_used(project_copy, tmp_path):
    other = tmp_path / "elsewhere"
    (other / "media").mkdir(parents=True)
    shutil.copy(tmp_path / "media" / "ntsc.mp4", other / "media" / "ntsc.mp4")
    rep = report(project_copy(root=str(other)))
    assert rep.root.moved is False
    assert by_name(rep, "ntsc.mp4").resolved_path == str(other / "media" / "ntsc.mp4")


def test_no_root_attribute_uses_project_dir(project_copy, tmp_path):
    rep = report(project_copy(root=None))
    assert rep.root.declared is None and not rep.root.moved
    assert by_name(rep, "ntsc.mp4").resolved_path == str(tmp_path / "media" / "ntsc.mp4")


def test_absolute_path_under_moved_root_is_rewritten(project_copy, tmp_path):
    rep = report(project_copy(resource="/old/place/media/ntsc.mp4", root="/old/place"))
    item = by_name(rep, "ntsc.mp4")
    assert item.rewritten_from_moved_root is True
    assert item.resolved_path == str(tmp_path / "media" / "ntsc.mp4")
    assert item.status is MediaStatus.OK


def test_currentpath_placeholder(project_copy, tmp_path):
    rep = report(project_copy(resource="$CURRENTPATH/media/ntsc.mp4", root="$CURRENTPATH"))
    assert by_name(rep, "ntsc.mp4").resolved_path == str(tmp_path / "media" / "ntsc.mp4")


def test_missing_file(project_copy, tmp_path):
    rep = report(project_copy(resource="media/gone.mp4"))
    item = by_name(rep, "gone.mp4")
    assert item.status is MediaStatus.MISSING
    assert rep.missing == [str(tmp_path / "media" / "gone.mp4")]


def test_symlinks(project_copy, tmp_path):
    (tmp_path / "media" / "link.mp4").symlink_to(tmp_path / "media" / "ntsc.mp4")
    (tmp_path / "media" / "dangling.mp4").symlink_to(tmp_path / "media" / "nowhere.mp4")
    good = by_name(report(project_copy(resource="media/link.mp4")), "link.mp4")
    assert good.status is MediaStatus.OK and good.symlink_target.endswith("ntsc.mp4")
    bad = by_name(report(project_copy(resource="media/dangling.mp4")), "dangling.mp4")
    assert bad.status is MediaStatus.MISSING and "Broken symbolic link" in bad.detail


def test_directory_is_inaccessible(project_copy):
    item = by_name(report(project_copy(resource="media")), "media")
    assert item.status is MediaStatus.INACCESSIBLE


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permissions")
def test_unreadable_file(project_copy, tmp_path):
    locked = tmp_path / "media" / "locked.mp4"
    shutil.copy(tmp_path / "media" / "ntsc.mp4", locked)
    locked.chmod(0)
    try:
        item = by_name(report(project_copy(resource="media/locked.mp4")), "locked.mp4")
        assert item.status is MediaStatus.INACCESSIBLE
    finally:
        locked.chmod(0o644)


@pytest.mark.parametrize("url", ["https://example.com/clip.mp4", "rtmp://host/live"])
def test_urls_are_never_fetched(project_copy, url):
    item = next(i for i in report(project_copy(resource=url)).items if i.reference == url)
    assert item.status is MediaStatus.REMOTE and item.resolved_path is None


def test_file_url_is_local(project_copy, tmp_path):
    ref = f"file://{tmp_path}/media/ntsc.mp4"
    assert (
        next(i for i in report(project_copy(resource=ref)).items if i.reference == ref).status
        is MediaStatus.OK
    )


def test_image_sequence_pattern(project_copy):
    item = next(
        i
        for i in report(project_copy(resource="media/frame_%05d.png")).items
        if i.reference == "media/frame_%05d.png"
    )
    assert item.status is MediaStatus.PATTERN


def test_malformed_reference_does_not_crash(project_copy):
    long_ref = "media/" + "x" * 5000 + ".mp4"  # ENAMETOOLONG
    item = next(i for i in report(project_copy(resource=long_ref)).items if i.reference == long_ref)
    assert item.status is MediaStatus.INACCESSIBLE


def test_bounded_results(project_copy):
    rep = report(project_copy(), max_items=1)
    assert rep.truncated and len(rep.items) == 1 and rep.total_items == 4
    with pytest.raises(ProjectError):
        report(project_copy(), max_items=0)


def test_media_is_never_modified(project_copy, tmp_path):
    files = sorted((tmp_path / "media").iterdir())
    before = [(f.read_bytes(), f.stat().st_mtime_ns) for f in files]
    report(project_copy(), probe=False)
    assert [(f.read_bytes(), f.stat().st_mtime_ns) for f in files] == before


# --------------------------------------------------------------- ffprobe (stubbed) ----
FFPROBE_JSON = {
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "r_frame_rate": "30000/1001",
            "duration": "2.002",
            "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}],
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "sample_rate": "48000",
            "channels": 2,
            "r_frame_rate": "0/0",
        },
    ],
    "format": {"format_name": "mov,mp4,m4a", "duration": "2.002000", "size": "1234"},
}


def _stub(make_stub, body):
    return str(make_stub("ffprobe", body))


def test_probe_parses_streams(make_stub, tmp_path):
    payload = tmp_path / "out.json"
    payload.write_text(json.dumps(FFPROBE_JSON))
    exe = _stub(make_stub, f'cat "{payload}"')
    info = media.probe_file(tmp_path / "x.mp4", exe)
    assert info.ok and info.format == "mov,mp4,m4a" and info.duration_seconds == 2.002
    video, audio = info.streams
    assert (video.frame_rate, video.rotation, video.width) == ("30000/1001", -90, 1920)
    assert (audio.sample_rate, audio.channels, audio.frame_rate) == (48000, 2, None)


def test_probe_passes_file_protocol_and_whitelist(make_stub, tmp_path):
    argsfile = tmp_path / "args"
    exe = _stub(make_stub, f'printf "%s\\n" "$@" > "{argsfile}"; echo "{{}}"')
    weird = tmp_path / "a:b -x.mp4"
    media.probe_file(weird, exe)
    args = argsfile.read_text().splitlines()
    assert args[-1] == f"file:{weird}"
    assert args[args.index("-protocol_whitelist") + 1] == "file"


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ('echo "moov atom not found" >&2; exit 1', "moov atom not found"),
        ("echo not-json", "invalid JSON"),
    ],
)
def test_probe_errors(make_stub, tmp_path, body, fragment):
    info = media.probe_file(tmp_path / "x.mp4", _stub(make_stub, body))
    assert not info.ok and fragment in (info.error or "")


def test_probe_timeout(make_stub, tmp_path):
    exe = _stub(make_stub, f"{shutil.which('sleep')} 30")
    info = media.probe_file(tmp_path / "x.mp4", exe, timeout=0.5)
    assert not info.ok and "did not finish" in (info.error or "")


def test_probe_without_ffprobe(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "locate", lambda _name: None)
    info = media.probe_file(tmp_path / "x.mp4")
    assert not info.ok and "diagnose_environment" in (info.error or "")


def test_inspect_file_refuses_urls_and_relative_paths():
    for bad in ("https://example.com/a.mp4", "relative.mp4", ""):
        with pytest.raises(ProjectError):
            media.inspect_file(bad)


# --------------------------------------------------------------- real ffprobe ---------
@pytest.mark.integration
@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe is not installed")
def test_real_ffprobe_on_fixture_media():
    rep = report(KD26 / "kd26-ntsc-timeline.kdenlive", probe=True)
    ntsc = by_name(rep, "ntsc.mp4").probe
    assert ntsc and ntsc.ok
    video = next(s for s in ntsc.streams if s.type == "video")
    assert (video.frame_rate, video.width, video.height) == ("30000/1001", 320, 180)
    wav = by_name(rep, "tone.wav").probe
    assert wav and [s.type for s in wav.streams] == ["audio"]
    corrupt = media.probe_file(Path(__file__))  # a Python file is not media
    assert not corrupt.ok
