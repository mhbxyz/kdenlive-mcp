from __future__ import annotations

import shutil
import time
from pathlib import Path

from mcp_kdenlive.environment import (
    DEFAULT_PROBE_TIMEOUT,
    TOOL_SPECS,
    ToolStatus,
    diagnose,
    probe_timeout_from_env,
    probe_tool,
)

SPEC = {s.name: s for s in TOOL_SPECS}


def test_all_available(all_tools_stubbed):
    report = diagnose(all_tools_stubbed)
    assert report.ready is True
    by_name = {t.name: t for t in report.tools}
    assert by_name["kdenlive"].version == "26.08.2"
    assert by_name["melt"].version == "7.42.0"
    assert by_name["ffprobe"].version == "n9.0.2"
    assert all(t.status is ToolStatus.AVAILABLE and t.source == "path" for t in report.tools)


def test_kdenlive_probe_runs_offscreen(make_stub, stub_env):
    # Real Kdenlive aborts without a display; the probe must force the offscreen platform.
    make_stub("kdenlive", '[ "$QT_QPA_PLATFORM" = offscreen ] || exit 134; echo "kdenlive 1.0"')
    report = probe_tool(SPEC["kdenlive"], stub_env, timeout=5)
    assert report.status is ToolStatus.AVAILABLE
    assert report.version == "1.0"


def test_missing_required_tool_makes_report_not_ready(make_stub, stub_env):
    make_stub("melt", 'echo "melt 7.42.0"')
    make_stub("ffprobe", 'echo "ffprobe version 7.1"')
    report = diagnose(stub_env)
    kdenlive = next(t for t in report.tools if t.name == "kdenlive")
    assert kdenlive.status is ToolStatus.MISSING
    assert kdenlive.hint and "KDENLIVE_MCP_KDENLIVE" in kdenlive.hint
    assert report.ready is False
    assert any("Flatpak" in note for note in report.notes)


def test_missing_optional_tool_keeps_report_ready(all_tools_stubbed, tmp_path):
    (tmp_path / "bin" / "ffmpeg").unlink()
    report = diagnose(all_tools_stubbed)
    assert report.ready is True
    assert next(t for t in report.tools if t.name == "ffmpeg").status is ToolStatus.MISSING


def test_override_takes_precedence_over_path(make_stub, stub_env, tmp_path):
    make_stub("melt", 'echo "melt 7.0.0"')
    other = tmp_path / "custom-melt"
    other.write_text('#!/bin/sh\necho "melt 7.99.1"\n')
    other.chmod(0o755)
    env = dict(stub_env, KDENLIVE_MCP_MELT=str(other))
    report = probe_tool(SPEC["melt"], env, timeout=5)
    assert (report.status, report.source, report.version) == (
        ToolStatus.AVAILABLE,
        "override",
        "7.99.1",
    )
    assert report.path == str(other)


def test_override_to_nonexistent_path(stub_env, tmp_path):
    env = dict(stub_env, KDENLIVE_MCP_FFPROBE=str(tmp_path / "nope"))
    report = probe_tool(SPEC["ffprobe"], env, timeout=5)
    assert report.status is ToolStatus.MISSING
    assert report.source == "override"
    assert "KDENLIVE_MCP_FFPROBE" in (report.hint or "")


def test_override_relative_name_not_on_path(stub_env):
    env = dict(stub_env, KDENLIVE_MCP_FFPROBE="ffprobe-nonexistent")
    report = probe_tool(SPEC["ffprobe"], env, timeout=5)
    assert report.status is ToolStatus.MISSING


def test_override_not_executable(stub_env, tmp_path):
    plain = tmp_path / "kdenlive.txt"
    plain.write_text("not a program")
    env = dict(stub_env, KDENLIVE_MCP_KDENLIVE=str(plain))
    report = probe_tool(SPEC["kdenlive"], env, timeout=5)
    assert report.status is ToolStatus.NOT_EXECUTABLE


def test_override_directory_is_not_executable(stub_env, tmp_path):
    env = dict(stub_env, KDENLIVE_MCP_KDENLIVE=str(tmp_path))
    assert probe_tool(SPEC["kdenlive"], env, timeout=5).status is ToolStatus.NOT_EXECUTABLE


def test_failing_program(make_stub, stub_env):
    make_stub("ffprobe", 'echo "boom: missing library" >&2; exit 127')
    report = probe_tool(SPEC["ffprobe"], stub_env, timeout=5)
    assert report.status is ToolStatus.FAILED
    assert "127" in (report.detail or "") and "missing library" in (report.detail or "")


def test_unrecognised_version_output(make_stub, stub_env):
    make_stub("melt", 'echo "something unexpected"')
    report = probe_tool(SPEC["melt"], stub_env, timeout=5)
    assert report.status is ToolStatus.VERSION_UNKNOWN
    assert report.version is None


def test_timeout(make_stub, stub_env):
    # The stub's child keeps the pipes open: the whole process group must be killed.
    make_stub("melt", f"{shutil.which('sleep')} 30 & {shutil.which('sleep')} 30")
    started = time.monotonic()
    report = probe_tool(SPEC["melt"], stub_env, timeout=0.5)
    assert report.status is ToolStatus.TIMEOUT
    assert time.monotonic() - started < 10


def test_unstartable_file(stub_env, tmp_path):
    bad = tmp_path / "bin" / "ffprobe"
    bad.write_bytes(b"\x7fELF garbage")
    bad.chmod(0o755)
    report = probe_tool(SPEC["ffprobe"], stub_env, timeout=5)
    assert report.status is ToolStatus.FAILED


def test_detail_is_bounded(make_stub, stub_env):
    make_stub(
        "melt",
        f'{shutil.which("head")} -c 20000 /dev/zero | {shutil.which("tr")} "\\\\0" x; exit 3',
    )
    report = probe_tool(SPEC["melt"], stub_env, timeout=5)
    assert report.status is ToolStatus.FAILED
    assert len(report.detail or "") <= 501


def test_shell_metacharacters_in_override_are_not_interpreted(stub_env, tmp_path):
    marker = tmp_path / "pwned"
    env = dict(stub_env, KDENLIVE_MCP_MELT=f"melt; touch {marker}")
    report = probe_tool(SPEC["melt"], env, timeout=5)
    assert report.status is ToolStatus.MISSING
    assert not marker.exists()


def test_probe_timeout_parsing():
    assert probe_timeout_from_env({}) == DEFAULT_PROBE_TIMEOUT
    assert probe_timeout_from_env({"KDENLIVE_MCP_PROBE_TIMEOUT": "2.5"}) == 2.5
    assert probe_timeout_from_env({"KDENLIVE_MCP_PROBE_TIMEOUT": "abc"}) == DEFAULT_PROBE_TIMEOUT
    assert probe_timeout_from_env({"KDENLIVE_MCP_PROBE_TIMEOUT": "-1"}) == DEFAULT_PROBE_TIMEOUT


def test_report_is_json_serialisable(all_tools_stubbed):
    data = diagnose(all_tools_stubbed).model_dump(mode="json")
    assert data["tools"][0]["status"] == "available"
    assert Path(data["tools"][0]["path"]).name == "kdenlive"
