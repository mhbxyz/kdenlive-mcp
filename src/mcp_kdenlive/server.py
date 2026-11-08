"""MCP transport layer: exposes domain operations as MCP tools over stdio.

Domain modules must not import this module. Nothing here writes to stdout except the
protocol itself; logs go to stderr.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from mcp_kdenlive import (
    __version__,
    create,
    edits,
    jobs,
    kdenlive_doc,
    markers,
    media,
    project,
    render,
    timeline_edit,
    writer,
)
from mcp_kdenlive import audio as audio_mod
from mcp_kdenlive.environment import EnvironmentReport, diagnose
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.markers import MarkerEditResult

SERVER_NAME = "kdenlive-mcp"

INSTRUCTIONS = """\
Local MCP server for Kdenlive projects on Linux.
Call diagnose_environment first to check which external programs (Kdenlive, MLT melt,
ffprobe) are available on this machine before using project or rendering tools.
The server works on local files only and never opens a network listener."""

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


Destination = Annotated[
    str,
    Field(
        description="Absolute path of the .kdenlive file to write (the source by default is "
        "never modified; see save_project_copy for the overwrite rules)."
    ),
]
Overwrite = Annotated[bool, Field(description="Allow replacing an existing destination file.")]
ExpectedSha = Annotated[
    str | None,
    Field(description="Required to write over the source: its sha256 as last read."),
]
EDIT = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)

ProjectPath = Annotated[
    str, Field(description="Absolute path of a .kdenlive project file on this machine.")
]


class EditResult(BaseModel):
    saved: writer.SaveResult
    markers: MarkerEditResult | None = None
    media: list[edits.MediaAdded] | None = None
    track: edits.TrackAdded | None = None
    timeline: timeline_edit.TimelineEditResult | None = None
    audio: audio_mod.AudioEditResult | None = None


Revision = Annotated[
    str,
    Field(
        description="sha256 of the project file (file.sha256 from get_timeline) that the clip "
        "ref comes from; the edit is refused if the file changed since."
    ),
]
ClipRef = Annotated[str, Field(description="Clip ref 'track:layer:entry' from get_timeline.")]
Sequence = Annotated[
    str | None, Field(description="Sequence uuid or name; default: active sequence.")
]
TIMELINE_RULES = (
    " Positions are frames at the project rate; nothing ripples and nothing is overwritten "
    "(overlaps fail). A clip grouped with its audio/video counterpart is edited together with "
    "it. The result is written to `destination` with the save_project_copy rules."
)


def _tool_error(exc: ProjectError) -> ToolError:
    return ToolError(str(exc))


BinId = Annotated[
    str | None,
    Field(description="Bin clip id for a clip marker; omit for a timeline guide."),
]


def _project_edit(
    path: str,
    destination: str,
    overwrite: bool,
    expected_sha256: str | None,
    operation: Callable[
        [writer.EditableProject],
        timeline_edit.TimelineEditResult | audio_mod.AudioEditResult | MarkerEditResult,
    ],
) -> EditResult:
    """Open, apply one operation, save; the result goes in its EditResult field."""
    try:
        edit = writer.open_for_edit(path)
        result = operation(edit)
        saved = writer.save(edit, destination, overwrite=overwrite, expected_sha256=expected_sha256)
        if isinstance(result, audio_mod.AudioEditResult):
            return EditResult(saved=saved, audio=result)
        if isinstance(result, MarkerEditResult):
            return EditResult(saved=saved, markers=result)
        return EditResult(saved=saved, timeline=result)
    except ProjectError as exc:
        raise _tool_error(exc) from exc


def _register_markers(server: MCPServer) -> None:
    marker_target = (
        " Without `bin_id` the marker is a timeline **guide** (position in timeline frames); with "
        "`bin_id` it is a **clip marker** on that bin clip (position in the clip's source frames, "
        "shown on every instance). Kdenlive keeps one marker per position. The result is written "
        "to `destination` with the save_project_copy rules."
    )

    @server.tool(
        name="list_markers",
        title="List guides and clip markers",
        annotations=READ_ONLY,
        description=(
            "List the timeline guides of a sequence, the clip markers of every bin clip, and the "
            "marker categories (index, name, color). Positions are frames: timeline frames for "
            "guides, source frames for clip markers. Read-only."
        ),
    )
    def list_markers(path: ProjectPath, sequence: Sequence = None) -> markers.MarkerList:
        try:
            return markers.list_markers(kdenlive_doc.load(path), sequence)
        except ProjectError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="add_marker",
        title="Add guide or clip marker",
        annotations=EDIT,
        description="Add a guide or a clip marker with a comment, a category and an optional "
        "duration in frames (0 = point marker)." + marker_target,
    )
    def add_marker(
        path: ProjectPath,
        position: Annotated[int, Field(ge=0)],
        destination: Destination,
        comment: str = "",
        category: Annotated[int, Field(ge=0)] = 0,
        duration: Annotated[int, Field(ge=0)] = 0,
        bin_id: BinId = None,
        sequence: Sequence = None,
        overwrite: Overwrite = False,
        expected_sha256: ExpectedSha = None,
    ) -> EditResult:
        return _project_edit(
            path,
            destination,
            overwrite,
            expected_sha256,
            lambda e: markers.add_marker(
                e,
                position,
                comment=comment,
                category=category,
                duration=duration,
                bin_id=bin_id,
                sequence=sequence,
            ),
        )

    @server.tool(
        name="update_marker",
        title="Update guide or clip marker",
        annotations=EDIT,
        description="Change the marker at `position`: move it (`new_position`), or change its "
        "comment, category or duration; omitted fields are kept." + marker_target,
    )
    def update_marker(
        path: ProjectPath,
        position: Annotated[int, Field(ge=0)],
        destination: Destination,
        new_position: Annotated[int, Field(ge=0)] | None = None,
        comment: str | None = None,
        category: Annotated[int, Field(ge=0)] | None = None,
        duration: Annotated[int, Field(ge=0)] | None = None,
        bin_id: BinId = None,
        sequence: Sequence = None,
        overwrite: Overwrite = False,
        expected_sha256: ExpectedSha = None,
    ) -> EditResult:
        return _project_edit(
            path,
            destination,
            overwrite,
            expected_sha256,
            lambda e: markers.update_marker(
                e,
                position,
                bin_id=bin_id,
                new_position=new_position,
                comment=comment,
                category=category,
                duration=duration,
                sequence=sequence,
            ),
        )

    @server.tool(
        name="remove_marker",
        title="Remove guide or clip marker",
        annotations=EDIT,
        description="Remove the marker at `position`." + marker_target,
    )
    def remove_marker(
        path: ProjectPath,
        position: Annotated[int, Field(ge=0)],
        destination: Destination,
        bin_id: BinId = None,
        sequence: Sequence = None,
        overwrite: Overwrite = False,
        expected_sha256: ExpectedSha = None,
    ) -> EditResult:
        return _project_edit(
            path,
            destination,
            overwrite,
            expected_sha256,
            lambda e: markers.remove_marker(e, position, bin_id=bin_id, sequence=sequence),
        )


def build_server(job_manager: jobs.JobManager | None = None) -> MCPServer:
    """Create the MCP server with every tool registered."""
    manager_holder: list[jobs.JobManager] = [job_manager] if job_manager else []

    def manager() -> jobs.JobManager:
        if not manager_holder:
            manager_holder.append(jobs.JobManager())
        return manager_holder[0]

    server = MCPServer(
        name=SERVER_NAME,
        title="Kdenlive MCP",
        description="Inspect, edit and render Kdenlive projects locally.",
        instructions=INSTRUCTIONS,
        version=__version__,
        website_url="https://github.com/mhbxyz/kdenlive-mcp",
        log_level="WARNING",
    )
    _register_inspection(server)
    _register_rendering(server, manager)
    _register_editing(server)
    _register_markers(server)
    return server


def _register_inspection(server: MCPServer) -> None:
    @server.tool(
        name="diagnose_environment",
        title="Diagnose environment",
        description=(
            "Report whether Kdenlive, MLT (melt), ffprobe and ffmpeg are installed and usable, "
            "with their paths, versions and actionable hints. Read-only: modifies nothing. "
            "Locations can be overridden with KDENLIVE_MCP_KDENLIVE, KDENLIVE_MCP_MELT, "
            "KDENLIVE_MCP_FFPROBE and KDENLIVE_MCP_FFMPEG in the server environment. "
            "`filesystem` lists the folders the tools may read and write "
            "(KDENLIVE_MCP_ALLOWED_ROOTS; by default the user's videos directory)."
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


def _register_rendering(server: MCPServer, manager: Callable[[], jobs.JobManager]) -> None:
    @server.tool(
        name="list_render_presets",
        title="List render presets",
        description=(
            "List the render presets of the installed Kdenlive (system and user custom "
            "presets), with group and file extension. Use a returned name with render_project."
        ),
        annotations=READ_ONLY,
    )
    def list_render_presets() -> render.PresetList:
        return render.list_presets()

    @server.tool(
        name="render_project",
        title="Render project",
        description=(
            "Render a Kdenlive project to a new local file with an installed Kdenlive preset "
            "(kdenlive --render), then validate the output with ffprobe. Blocks until the "
            "render ends or `timeout_seconds` elapses. Never modifies the project or its media. "
            "Refuses an existing output unless overwrite=true (the old file is replaced only "
            "after a successful, validated render), unknown presets, and projects with missing "
            "media unless allow_missing_media=true. Renders the file on disk, not unsaved "
            "changes in a running Kdenlive."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def render_project(
        path: ProjectPath,
        output: Annotated[str, Field(description="Absolute path of the file to create.")],
        preset: Annotated[
            str, Field(description="Kdenlive preset name (see list_render_presets).")
        ] = render.DEFAULT_PRESET,
        overwrite: Annotated[bool, Field(description="Replace an existing output file.")] = False,
        allow_missing_media: Annotated[
            bool, Field(description="Render even if media files are missing.")
        ] = False,
        timeout_seconds: Annotated[
            float, Field(gt=0, le=render.MAX_TIMEOUT, description="Stop the render after this.")
        ] = render.DEFAULT_TIMEOUT,
    ) -> render.RenderResult:
        try:
            plan = render.prepare(
                path, output, preset, overwrite=overwrite, allow_missing_media=allow_missing_media
            )
            return render.render(plan, timeout=timeout_seconds)
        except ProjectError as exc:
            raise _tool_error(exc) from exc

    job_annotations = ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
    )

    @server.tool(
        name="start_render",
        title="Start render job",
        description=(
            "Start rendering a Kdenlive project in the background and return a job id "
            "immediately. Same checks and guarantees as render_project (preset, output, "
            "missing media, atomic publication after validation). Poll get_render_job for the "
            "state (queued, running, succeeded, failed, cancelled) and cancel with "
            "cancel_render_job. Jobs live in memory and are stopped when the server exits."
        ),
        annotations=job_annotations,
    )
    def start_render(
        path: ProjectPath,
        output: Annotated[str, Field(description="Absolute path of the file to create.")],
        preset: Annotated[
            str, Field(description="Kdenlive preset name (see list_render_presets).")
        ] = render.DEFAULT_PRESET,
        overwrite: Annotated[bool, Field(description="Replace an existing output file.")] = False,
        allow_missing_media: Annotated[
            bool, Field(description="Render even if media files are missing.")
        ] = False,
        timeout_seconds: Annotated[
            float, Field(gt=0, le=render.MAX_TIMEOUT, description="Stop the render after this.")
        ] = render.DEFAULT_TIMEOUT,
    ) -> jobs.JobInfo:
        try:
            plan = render.prepare(
                path, output, preset, overwrite=overwrite, allow_missing_media=allow_missing_media
            )
            return manager().submit(plan, timeout_seconds)
        except ProjectError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="get_render_job",
        title="Get render job",
        description=(
            "State of a render job: queued (with queue position), running (elapsed time; "
            "progress only when Kdenlive reports it), or ended with the full result "
            "(exit status, output validation, log tail)."
        ),
        annotations=READ_ONLY,
    )
    def get_render_job(
        job_id: Annotated[str, Field(description="Id returned by start_render.")],
    ) -> jobs.JobInfo:
        try:
            return manager().get(job_id)
        except ProjectError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="list_render_jobs",
        title="List render jobs",
        description="List render jobs of this server session, most recent first.",
        annotations=READ_ONLY,
    )
    def list_render_jobs() -> jobs.JobList:
        return manager().list()

    @server.tool(
        name="cancel_render_job",
        title="Cancel render job",
        description=(
            "Request cancellation of a queued or running render job. The returned job has "
            "cancel_requested=true; its state becomes 'cancelled' once Kdenlive and its child "
            "processes have exited and the partial output was removed. Cancelling a finished "
            "job changes nothing."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def cancel_render_job(
        job_id: Annotated[str, Field(description="Id returned by start_render.")],
    ) -> jobs.JobInfo:
        try:
            return manager().cancel(job_id)
        except ProjectError as exc:
            raise _tool_error(exc) from exc


def _register_editing(server: MCPServer) -> None:
    @server.tool(
        name="list_profiles",
        title="List project profiles",
        description=(
            "List the MLT video profiles available for new projects (name, resolution, "
            "rational frame rate, progressive)."
        ),
        annotations=READ_ONLY,
    )
    def list_profiles() -> list[create.ProfileInfo]:
        return create.list_profiles()

    @server.tool(
        name="create_project",
        title="Create project",
        description=(
            "Create a new Kdenlive project file containing one media clip placed at the start "
            "of the timeline (video on V1, audio on A1, grouped). The project is modeled on "
            "Kdenlive 26.08 files and stays editable in Kdenlive. `profile` is an MLT profile "
            "name (see list_profiles) or 'auto' to use the standard profile matching the "
            "media. Refuses to replace an existing file unless overwrite=true; writes "
            "atomically and never modifies the media. Media inside the project's directory "
            "is stored with a relative path."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def create_project(
        output: Annotated[str, Field(description="Absolute path of the new .kdenlive file.")],
        media_path: Annotated[str, Field(description="Absolute path of an audio or video file.")],
        profile: Annotated[
            str, Field(description="MLT profile name, or 'auto' to match the media.")
        ] = "auto",
        overwrite: Annotated[bool, Field(description="Replace an existing project file.")] = False,
    ) -> create.CreateResult:
        try:
            return create.create_project(output, media_path, profile, overwrite=overwrite)
        except ProjectError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="save_project_copy",
        title="Save project copy",
        description=(
            "Write a verified copy of a Kdenlive project to another .kdenlive file, preserving "
            "all project data (unknown metadata included). Relative media paths are rewritten so "
            "they still work from the destination folder. Refuses to overwrite an existing file "
            "unless overwrite=true (a timestamped backup is kept), refuses to write over the "
            "source unless overwrite=true and expected_sha256 (from inspect_project) match, and "
            "refuses if the source changed since it was read or the destination is open in "
            "Kdenlive."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def save_project_copy(
        path: ProjectPath,
        destination: Annotated[str, Field(description="Absolute path of the .kdenlive to write.")],
        overwrite: Annotated[bool, Field(description="Allow replacing an existing file.")] = False,
        expected_sha256: Annotated[
            str | None,
            Field(description="Required to write over the source: its sha256 as last read."),
        ] = None,
    ) -> writer.SaveResult:
        try:
            edit = writer.open_for_edit(path)
            return writer.save(
                edit, destination, overwrite=overwrite, expected_sha256=expected_sha256
            )
        except ProjectError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="add_media_to_bin",
        title="Add media to bin",
        description=(
            "Add audio or video files to a project's bin (not to the timeline) and write the "
            "result to `destination`. Files already in the bin are reported as 'existing' with "
            "their clip id instead of being duplicated. Clip data comes from MLT, as in "
            "Kdenlive. Same safety rules as save_project_copy."
        ),
        annotations=EDIT,
    )
    def add_media_to_bin(
        path: ProjectPath,
        media_paths: Annotated[list[str], Field(description="Absolute paths of media files.")],
        destination: Destination,
        overwrite: Overwrite = False,
        expected_sha256: ExpectedSha = None,
    ) -> EditResult:
        try:
            edit = writer.open_for_edit(path)
            added = edits.add_media(edit, media_paths)
            saved = writer.save(
                edit, destination, overwrite=overwrite, expected_sha256=expected_sha256
            )
            return EditResult(saved=saved, media=added)
        except ProjectError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="add_track",
        title="Add track",
        description=(
            "Add an empty, optionally named audio or video track to a sequence and write the "
            "result to `destination`. Video tracks are added on top of the existing video "
            "tracks; audio tracks below the existing audio tracks (Kdenlive's track order). "
            "Existing clips, groups, compositions and mixes are preserved. Same safety rules "
            "as save_project_copy."
        ),
        annotations=EDIT,
    )
    def add_track(
        path: ProjectPath,
        kind: Annotated[str, Field(description="'audio' or 'video'.", pattern="^(audio|video)$")],
        destination: Destination,
        name: Annotated[str | None, Field(description="Track name shown in Kdenlive.")] = None,
        sequence: Annotated[
            str | None, Field(description="Sequence uuid or name; default: active sequence.")
        ] = None,
        overwrite: Overwrite = False,
        expected_sha256: ExpectedSha = None,
    ) -> EditResult:
        try:
            edit = writer.open_for_edit(path)
            track = edits.add_track(edit, kind, name, sequence)
            saved = writer.save(
                edit, destination, overwrite=overwrite, expected_sha256=expected_sha256
            )
            return EditResult(saved=saved, track=track)
        except ProjectError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="insert_clip",
        title="Insert clip",
        annotations=EDIT,
        description=(
            "Place a bin clip (bin_id from list_project_media / get_timeline) on a track at "
            "frame `start`, optionally a source sub-range [source_in, source_out] (inclusive). "
            "A clip with audio and video put on a video track also gets its audio on "
            "`audio_track` (default A1; null for video only), grouped like Kdenlive."
            + TIMELINE_RULES
        ),
    )
    def insert_clip(
        path: ProjectPath,
        bin_id: str,
        track: Annotated[str, Field(description="Track label, e.g. V1.")],
        start: Annotated[int, Field(ge=0)],
        destination: Destination,
        source_in: Annotated[int, Field(ge=0)] = 0,
        source_out: int | None = None,
        audio_track: str | None = "A1",
        sequence: Sequence = None,
        overwrite: Overwrite = False,
        expected_sha256: ExpectedSha = None,
    ) -> EditResult:
        return _project_edit(
            path,
            destination,
            overwrite,
            expected_sha256,
            lambda e: timeline_edit.insert_clip(
                e, bin_id, track, start, source_in, source_out, audio_track, sequence
            ),
        )

    @server.tool(
        name="move_clip",
        title="Move clip",
        annotations=EDIT,
        description=(
            "Move a clip to frame `start`, optionally to another track of the same kind."
            + TIMELINE_RULES
        ),
    )
    def move_clip(
        path: ProjectPath,
        clip: ClipRef,
        revision: Revision,
        start: Annotated[int, Field(ge=0)],
        destination: Destination,
        track: str | None = None,
        sequence: Sequence = None,
        overwrite: Overwrite = False,
        expected_sha256: ExpectedSha = None,
    ) -> EditResult:
        return _project_edit(
            path,
            destination,
            overwrite,
            expected_sha256,
            lambda e: timeline_edit.move_clip(e, clip, revision, start, track, sequence),
        )

    @server.tool(
        name="trim_clip",
        title="Trim clip",
        annotations=EDIT,
        description=(
            "Change a clip's timeline bounds to [start, end) (either may be omitted); the source "
            "in/out points follow, like resizing in Kdenlive. Extending is limited by the source "
            "length." + TIMELINE_RULES
        ),
    )
    def trim_clip(
        path: ProjectPath,
        clip: ClipRef,
        revision: Revision,
        destination: Destination,
        start: int | None = None,
        end: int | None = None,
        sequence: Sequence = None,
        overwrite: Overwrite = False,
        expected_sha256: ExpectedSha = None,
    ) -> EditResult:
        return _project_edit(
            path,
            destination,
            overwrite,
            expected_sha256,
            lambda e: timeline_edit.trim_clip(e, clip, revision, start, end, sequence),
        )

    @server.tool(
        name="split_clip",
        title="Split clip",
        annotations=EDIT,
        description=(
            "Cut a clip at timeline frame `at` into two adjacent clips (razor)." + TIMELINE_RULES
        ),
    )
    def split_clip(
        path: ProjectPath,
        clip: ClipRef,
        revision: Revision,
        at: Annotated[int, Field(ge=1)],
        destination: Destination,
        sequence: Sequence = None,
        overwrite: Overwrite = False,
        expected_sha256: ExpectedSha = None,
    ) -> EditResult:
        return _project_edit(
            path,
            destination,
            overwrite,
            expected_sha256,
            lambda e: timeline_edit.split_clip(e, clip, revision, at, sequence),
        )

    @server.tool(
        name="remove_clip",
        title="Remove clip",
        annotations=EDIT,
        description=(
            "Remove a clip from the timeline, leaving a gap (neighbours do not move; the bin clip "
            "stays)." + TIMELINE_RULES
        ),
    )
    def remove_clip(
        path: ProjectPath,
        clip: ClipRef,
        revision: Revision,
        destination: Destination,
        sequence: Sequence = None,
        overwrite: Overwrite = False,
        expected_sha256: ExpectedSha = None,
    ) -> EditResult:
        return _project_edit(
            path,
            destination,
            overwrite,
            expected_sha256,
            lambda e: timeline_edit.remove_clip(e, clip, revision, sequence),
        )

    @server.tool(
        name="set_clip_volume",
        title="Set clip volume",
        annotations=EDIT,
        description=(
            "Set a constant audio level in dB (-100 to +20; 0 = unchanged level) on a clip's "
            "audio, with Kdenlive's Volume effect. For an audio/video pair, the audio clip is "
            "changed. An existing constant level is replaced; an animated one is refused. "
            "The result is written to `destination` with the save_project_copy rules."
        ),
    )
    def set_clip_volume(
        path: ProjectPath,
        clip: ClipRef,
        revision: Revision,
        level_db: Annotated[float, Field(ge=-100, le=20)],
        destination: Destination,
        sequence: Sequence = None,
        overwrite: Overwrite = False,
        expected_sha256: ExpectedSha = None,
    ) -> EditResult:
        return _project_edit(
            path,
            destination,
            overwrite,
            expected_sha256,
            lambda e: audio_mod.set_clip_volume(e, clip, revision, level_db, sequence),
        )

    @server.tool(
        name="set_clip_fades",
        title="Set clip audio fades",
        annotations=EDIT,
        description=(
            "Set the audio fade-in and/or fade-out of a clip, in frames (omit to keep, 0 to "
            "remove), with Kdenlive's Fade in / Fade out effects. Both must fit in the clip. "
            "For an audio/video pair, the audio clip is changed. Fades stay at the clip's edges "
            "when it is later trimmed or split. The result is written to `destination` with the "
            "save_project_copy rules."
        ),
    )
    def set_clip_fades(
        path: ProjectPath,
        clip: ClipRef,
        revision: Revision,
        destination: Destination,
        fade_in: Annotated[int, Field(ge=0)] | None = None,
        fade_out: Annotated[int, Field(ge=0)] | None = None,
        sequence: Sequence = None,
        overwrite: Overwrite = False,
        expected_sha256: ExpectedSha = None,
    ) -> EditResult:
        return _project_edit(
            path,
            destination,
            overwrite,
            expected_sha256,
            lambda e: audio_mod.set_clip_fades(e, clip, revision, fade_in, fade_out, sequence),
        )
