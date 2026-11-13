"""The server's MCP instructions carry the editing method (docs/research/orchestration.md).

Clients read them before any tool: Claude Code keeps 4096 characters, and Codex's docs advise
putting what matters in the first 512. Every tool they name must exist.
"""

from __future__ import annotations

import re

import pytest
from mcp import Client

from mcp_kdenlive.server import INSTRUCTIONS, build_server


def test_instructions_fit_the_clients_limits():
    assert len(INSTRUCTIONS) <= 4096
    head = INSTRUCTIONS[:512]
    for essential in ("in place", "overwrite=true", "saved.sha256", "revision", "check",
                      "cannot see or hear"):  # fmt: skip
        assert essential in head, essential


@pytest.mark.anyio
async def test_instructions_reach_the_client_and_name_real_tools():
    async with Client(build_server()) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        served = client.instructions
    assert served == INSTRUCTIONS
    named = set(re.findall(r"\b([a-z]+(?:_[a-z]+)+)\b", INSTRUCTIONS))
    named -= {"saved", "stale_revision"}  # a result field and an error code, not tools
    assert named <= tools, named - tools
