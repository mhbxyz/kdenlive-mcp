"""MCP transport layer: exposes domain operations as MCP tools over stdio.

Domain modules must not import this module. Nothing here writes to stdout except the
protocol itself; logs go to stderr.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from mcp_kdenlive import __version__
from mcp_kdenlive.environment import EnvironmentReport, diagnose

SERVER_NAME = "kdenlive-mcp"

INSTRUCTIONS = """\
Local MCP server for Kdenlive projects on Linux.
Call diagnose_environment first to check which external programs (Kdenlive, MLT melt,
ffprobe) are available on this machine before using project or rendering tools.
The server works on local files only and never opens a network listener."""

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


def build_server() -> MCPServer:
    """Create the MCP server with every tool registered."""
    server = MCPServer(
        name=SERVER_NAME,
        title="Kdenlive MCP",
        description="Inspect, edit and render Kdenlive projects locally.",
        instructions=INSTRUCTIONS,
        version=__version__,
        website_url="https://github.com/mhbxyz/kdenlive-mcp",
        log_level="WARNING",
    )

    @server.tool(
        name="diagnose_environment",
        title="Diagnose environment",
        description=(
            "Report whether Kdenlive, MLT (melt), ffprobe and ffmpeg are installed and usable, "
            "with their paths, versions and actionable hints. Read-only: modifies nothing. "
            "Locations can be overridden with KDENLIVE_MCP_KDENLIVE, KDENLIVE_MCP_MELT, "
            "KDENLIVE_MCP_FFPROBE and KDENLIVE_MCP_FFMPEG in the server environment."
        ),
        annotations=READ_ONLY,
    )
    def diagnose_environment() -> EnvironmentReport:
        return diagnose()

    return server
