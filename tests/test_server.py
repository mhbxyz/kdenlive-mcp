"""MCP-level tests: discovery, structured results, stdout cleanliness over real stdio."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from mcp_kdenlive.server import build_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_tool_discovery_and_annotations():
    async with Client(build_server()) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    tool = tools["diagnose_environment"]
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert set(tool.output_schema["properties"]) >= {"ready", "tools", "notes"}
    assert "KDENLIVE_MCP_KDENLIVE" in tool.description


async def test_diagnose_returns_structured_content(all_tools_stubbed, monkeypatch):
    for key, value in all_tools_stubbed.items():
        monkeypatch.setenv(key, value)
    async with Client(build_server()) as client:
        result = await client.call_tool("diagnose_environment", {})
    assert result.is_error is False
    data = result.structured_content
    assert data["ready"] is True
    assert {t["name"] for t in data["tools"]} == {"kdenlive", "melt", "ffprobe", "ffmpeg"}


async def test_unknown_tool_is_an_error_result():
    async with Client(build_server()) as client:
        result = await client.call_tool("does_not_exist", {})
    assert result.is_error is True


async def test_stdio_subprocess_end_to_end(all_tools_stubbed):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_kdenlive"], env=all_tools_stubbed
    )
    async with Client(params) as client:
        assert client.server_info.name == "kdenlive-mcp"
        result = await client.call_tool("diagnose_environment", {})
    assert result.structured_content["ready"] is True


def _rpc(msg_id, method, params=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        msg["id"] = msg_id
    if params is not None:
        msg["params"] = params
    return json.dumps(msg) + "\n"


def test_stdout_is_protocol_clean_with_handshake_client(all_tools_stubbed):
    """A 2025-era client (initialize handshake) gets only JSON-RPC lines on stdout."""
    env = dict(os.environ, **all_tools_stubbed)
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_kdenlive", "--log-level", "DEBUG"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    received: list[dict] = []

    def request(msg_id, method, params=None):
        proc.stdin.write(_rpc(msg_id, method, params))
        proc.stdin.flush()
        if msg_id is None:
            return None
        while True:
            line = proc.stdout.readline()
            assert line, f"server closed stdout; stderr: {proc.stderr.read()}"
            message = json.loads(line)  # every stdout line must be JSON-RPC
            assert message.get("jsonrpc") == "2.0"
            received.append(message)
            if message.get("id") == msg_id:
                return message

    try:
        init = request(
            1,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "raw-test", "version": "0"},
            },
        )
        assert init["result"]["serverInfo"]["name"] == "kdenlive-mcp"
        request(None, "notifications/initialized")
        listed = request(2, "tools/list", {})
        assert "diagnose_environment" in {t["name"] for t in listed["result"]["tools"]}
        called = request(3, "tools/call", {"name": "diagnose_environment", "arguments": {}})
        assert called["result"]["isError"] is False
        assert called["result"]["structuredContent"]["ready"] is True
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)
        proc.stdout.close()
        proc.stderr.close()


def test_invalid_json_does_not_corrupt_stdout(all_tools_stubbed):
    env = dict(os.environ, **all_tools_stubbed)
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_kdenlive"],
        input="this is not json\n",
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )
    for line in proc.stdout.splitlines():
        if line.strip():
            assert json.loads(line)["jsonrpc"] == "2.0"


FIXTURE = (Path(__file__).parent / "fixtures" / "kd26" / "kd26-ntsc-timeline.kdenlive").resolve()


async def test_inspect_project_tool():
    async with Client(build_server()) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert tools["inspect_project"].annotations.read_only_hint is True
        result = await client.call_tool("inspect_project", {"path": str(FIXTURE)})
    assert result.is_error is False
    data = result.structured_content
    assert data["profile"]["frame_rate"] == {"num": 30000, "den": 1001, "fps": 29.97003}
    assert data["sequences"][0]["duration_frames"] == 60


async def test_get_timeline_tool_and_bounds():
    async with Client(build_server()) as client:
        result = await client.call_tool("get_timeline", {"path": str(FIXTURE), "max_clips": 1})
        bad = await client.call_tool("get_timeline", {"path": str(FIXTURE), "max_clips": 0})
    assert result.is_error is False
    assert result.structured_content["truncated"] is True
    assert result.structured_content["returned_clips"] == 1
    assert bad.is_error is True


async def test_project_errors_are_actionable():
    async with Client(build_server()) as client:
        relative = await client.call_tool("inspect_project", {"path": "project.kdenlive"})
        missing = await client.call_tool("inspect_project", {"path": "/nonexistent/x.kdenlive"})
    assert relative.is_error and "[invalid_path]" in relative.content[0].text
    assert "absolute path" in relative.content[0].text
    assert missing.is_error and "[not_found]" in missing.content[0].text
