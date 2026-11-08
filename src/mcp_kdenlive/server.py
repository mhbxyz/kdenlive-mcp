"""MCP transport layer: exposes domain operations as MCP tools over stdio.

Domain modules must not import this module. Nothing here writes to stdout except the
protocol itself; logs go to stderr.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from mcp_kdenlive import __version__, kdenlive_doc, media, project
from mcp_kdenlive.environment import EnvironmentReport, diagnose
from mcp_kdenlive.kdenlive_doc import ProjectError

SERVER_NAME = "kdenlive-mcp"

INSTRUCTIONS = """\
Local MCP server for Kdenlive projects on Linux.
Call diagnose_environment first to check which external programs (Kdenlive, MLT melt,
ffprobe) are available on this machine before using project or rendering tools.
The server works on local files only and never opens a network listener."""

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


ProjectPath = Annotated[
    str, Field(description="Absolute path of a .kdenlive project file on this machine.")
]


def _tool_error(exc: ProjectError) -> ToolError:
    return ToolError(str(exc))


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

    @server.tool(
        name="inspect_project",
        title="Inspect project",
        description=(
            "Summarize a Kdenlive project file without modifying it: Kdenlive and document "
            "versions, profile (resolution, rational frame rate), bin clip count and each "
            "timeline sequence with its duration and track counts. Includes the file's sha256 "
            "so later calls can detect changes. Reads the file on disk only: unsaved changes "
            "in a running Kdenlive are not visible."
        ),
        annotations=READ_ONLY,
    )
    def inspect_project(path: ProjectPath) -> project.ProjectSummary:
        try:
            return project.summarize(kdenlive_doc.load(path))
        except ProjectError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="get_timeline",
        title="Get timeline",
        description=(
            "Return the tracks, clips, mixes, compositions and guides of one sequence of a "
            "Kdenlive project, read-only. Positions are integer frames at the project frame "
            "rate; timeline ranges are [start, end) and source in/out are inclusive (see the "
            "'conventions' field). Large timelines are truncated to max_clips."
        ),
        annotations=READ_ONLY,
    )
    def get_timeline(
        path: ProjectPath,
        sequence: Annotated[
            str | None,
            Field(description="Sequence uuid or name; defaults to the active sequence."),
        ] = None,
        max_clips: Annotated[
            int, Field(ge=1, le=project.MAX_CLIPS_LIMIT, description="Maximum clips returned.")
        ] = project.DEFAULT_MAX_CLIPS,
    ) -> project.Timeline:
        try:
            return project.timeline(kdenlive_doc.load(path), sequence, max_clips)
        except ProjectError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="list_project_media",
        title="List project media",
        description=(
            "List the media referenced by a Kdenlive project's bin and diagnose missing or "
            "unreadable files, plus files used by effects and transitions (LUTs, luma "
            "wipes, subtitles). References are resolved exactly like Kdenlive does (project "
            "root, moved projects, proxies). Read-only; URLs are reported but never fetched. "
            "With probe=true, ffprobe metadata (streams, codecs, sizes, frame rates, rotation) "
            "is added for up to 50 available files."
        ),
        annotations=READ_ONLY,
    )
    def list_project_media(
        path: ProjectPath,
        probe: Annotated[bool, Field(description="Run ffprobe on available files.")] = False,
        max_items: Annotated[
            int, Field(ge=1, le=media.MAX_ITEMS_LIMIT, description="Maximum bin items returned.")
        ] = media.DEFAULT_MAX_ITEMS,
    ) -> media.MediaReport:
        try:
            return media.media_report(kdenlive_doc.load(path), probe=probe, max_items=max_items)
        except ProjectError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="probe_media",
        title="Probe media file",
        description=(
            "Check one local media file (absolute path) and return ffprobe metadata: "
            "duration, streams, codecs, dimensions, rational frame rate, sample rate, "
            "channels, rotation. Read-only; URLs are refused. Probing reads headers and does "
            "not prove that every frame decodes."
        ),
        annotations=READ_ONLY,
    )
    def probe_media(
        path: Annotated[str, Field(description="Absolute path of a local media file.")],
    ) -> media.MediaFileReport:
        try:
            return media.inspect_file(path)
        except ProjectError as exc:
            raise _tool_error(exc) from exc

    return server
