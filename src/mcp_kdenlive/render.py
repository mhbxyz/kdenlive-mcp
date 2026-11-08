"""Local rendering through Kdenlive's command line (`kdenlive --render`), with validation.

Evidence behind the design (docs/research/integration-surfaces.md §2.3, issue #6):

* ``kdenlive --render`` exits 0 even when media is missing, and also when the preset name is
  unknown (it silently renders with another preset), so presets and media are checked first.
* It crashes (SIGSEGV) on a nonexistent project and refuses an existing output file.
* It loads the project from disk; a running Kdenlive instance and its unsaved changes are
  never involved.
* It works headless (``QT_QPA_PLATFORM=offscreen``) and without a session D-Bus. Each render
  gets a private, temporary ``XDG_CONFIG_HOME``/``XDG_CACHE_HOME`` so the user's Kdenlive
  configuration and caches are never touched; ``XDG_DATA_HOME`` is kept for custom presets.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from mcp_kdenlive import kdenlive_doc, media, project, safexml
from mcp_kdenlive.environment import locate
from mcp_kdenlive.kdenlive_doc import ProjectError
from mcp_kdenlive.process import ProcessTimeout, Running, run, start

DEFAULT_PRESET = "MP4-H264/AAC"
DEFAULT_TIMEOUT = 3600.0
MAX_TIMEOUT = 24 * 3600.0
LOG_TAIL_LINES = 40
_NOISE = (
    "mlt_repository",
    "Duplicate ",
    "failed to dlopen",
    "cannot open shared object",
    "No such file or directory)",
    "VDPAU",
    "libva",
)


# ---------------------------------------------------------------- presets ------------
class Preset(BaseModel):
    name: str = Field(description="Name accepted by kdenlive --render-preset.")
    group: str | None
    extension: str | None = Field(description="File extension Kdenlive uses for this preset.")
    source: str = Field(description="Preset file it was read from.")
    custom: bool = Field(description="True for user presets (they override system ones).")


class PresetList(BaseModel):
    presets: list[Preset]
    sources: list[str]
    notes: list[str]


def _data_dirs(env: Mapping[str, str]) -> tuple[Path, list[Path]]:
    home = Path(env.get("HOME", str(Path.home())))
    data_home = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
    dirs = [
        Path(d) for d in (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if d
    ]
    return data_home, dirs


def preset_files(env: Mapping[str, str] | None = None) -> list[tuple[Path, bool]]:
    """Preset files in Kdenlive's reading order: system profiles.xml, user files, customprofiles."""
    environ = dict(os.environ if env is None else env)
    data_home, data_dirs = _data_dirs(environ)
    files: list[tuple[Path, bool]] = []
    candidates = [data_home / "kdenlive" / "export" / "profiles.xml"]
    candidates += [d / "kdenlive" / "export" / "profiles.xml" for d in data_dirs]
    exe = locate("kdenlive", environ)
    if exe:  # relocatable installs (AppImage, /opt): <prefix>/bin/kdenlive -> <prefix>/share
        candidates.append(
            Path(exe).resolve().parent.parent / "share" / "kdenlive" / "export" / "profiles.xml"
        )
    system = next((c for c in candidates if c.is_file()), None)
    if system:
        files.append((system, False))
    user_dir = data_home / "kdenlive" / "export"
    if user_dir.is_dir():
        extra = sorted(
            p
            for p in user_dir.glob("*.xml")
            if p.name not in ("profiles.xml", "customprofiles.xml")
        )
        files += [(p, True) for p in extra]
        if (user_dir / "customprofiles.xml").is_file():
            files.append((user_dir / "customprofiles.xml", True))
    return files


def _parse_presets(path: Path, custom: bool) -> list[Preset]:
    try:
        root, _ = safexml.parse_file(path, max_bytes=8 * 1024 * 1024)
    except (OSError, ValueError, safexml.ParseError):
        return []
    found = []
    for group in root.iter("group"):
        name_el = group.find("name")
        group_name = (name_el.text or "").strip() if name_el is not None else group.get("id")
        for prof in group.findall("profile"):
            if prof.get("name"):
                found.append(
                    Preset(
                        name=prof.get("name", ""),
                        group=group_name,
                        extension=prof.get("extension"),
                        source=str(path),
                        custom=custom,
                    )
                )
    # customprofiles.xml uses <profile> elements directly under the root
    for prof in root.findall("profile"):
        if prof.get("name"):
            found.append(
                Preset(
                    name=prof.get("name", ""),
                    group=prof.get("category"),
                    extension=prof.get("extension"),
                    source=str(path),
                    custom=custom,
                )
            )
    return found


def list_presets(env: Mapping[str, str] | None = None) -> PresetList:
    files = preset_files(env)
    by_name: dict[str, Preset] = {}
    for path, custom in files:
        for preset in _parse_presets(path, custom):
            by_name[preset.name] = preset  # later files override, like Kdenlive
    notes = [
        "Presets are read from Kdenlive's preset files; whether the installed MLT/FFmpeg "
        "supports every codec is only known when rendering.",
    ]
    if not files:
        notes.append("No Kdenlive preset file was found; is Kdenlive installed?")
    return PresetList(
        presets=sorted(by_name.values(), key=lambda p: (p.custom is False, p.group or "", p.name)),
        sources=[str(p) for p, _ in files],
        notes=notes,
    )


# ---------------------------------------------------------------- results ------------
class RenderState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OutputValidation(BaseModel):
    ok: bool
    format: str | None = None
    duration_seconds: float | None = None
    expected_duration_seconds: float | None = None
    video_streams: int = 0
    audio_streams: int = 0
    issues: list[str] = Field(default_factory=list)
    note: str = (
        "Validation checks streams and duration with ffprobe; it does not judge visual "
        "or audio correctness."
    )


class RenderResult(BaseModel):
    state: RenderState
    project: str
    project_sha256: str
    sequence: str | None
    output: str
    preset: str
    exit_code: int | None = None
    elapsed_seconds: float | None = None
    process_completed: bool = Field(description="Kdenlive's render process exited with status 0.")
    validation: OutputValidation | None = Field(
        default=None, description="ffprobe check of the produced file (separate from completion)."
    )
    error: str | None = None
    log_tail: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- capability ---------
_CLI_SUPPORT: dict[tuple[str, int], bool] = {}


def supports_cli_render(kdenlive: str, env: Mapping[str, str] | None = None) -> bool:
    """True if Kdenlive accepts --render/--render-preset (absent in 23.08, present in 26.08)."""
    try:
        key = (kdenlive, Path(kdenlive).stat().st_mtime_ns)
    except OSError:
        return False
    if key not in _CLI_SUPPORT:
        environ = dict(os.environ if env is None else env, QT_QPA_PLATFORM="offscreen")
        try:
            done = run([kdenlive, "--help"], timeout=30, env=environ)
            text = done.stdout + done.stderr
            _CLI_SUPPORT[key] = "--render-preset" in text and "--render" in text
        except (OSError, ProcessTimeout):
            return False
    return _CLI_SUPPORT[key]


# ---------------------------------------------------------------- preflight ----------
@dataclass
class RenderPlan:
    project_path: Path
    project_sha256: str
    output: Path
    preset: Preset
    overwrite: bool
    kdenlive: str
    expected_seconds: float | None
    sequence: str | None
    temp_output: Path
    warnings: list[str] = field(default_factory=list)


def _bounded_tail(text: str) -> list[str]:
    lines = [ln for ln in text.splitlines() if ln.strip() and not any(n in ln for n in _NOISE)]
    return [ln[:300] for ln in lines[-LOG_TAIL_LINES:]]


def _check_output(output: str, project_path: Path, *, overwrite: bool) -> Path:
    out = Path(output).expanduser()
    if not output or not out.is_absolute():
        raise ProjectError("invalid_output", f"{output!r} is not an absolute output path.")
    if out.resolve() == project_path.resolve():
        raise ProjectError("invalid_output", "The output path is the project file itself.")
    if out.exists() and out.is_dir():
        raise ProjectError("invalid_output", f"{out} is a directory.")
    if out.exists() and not overwrite:
        raise ProjectError(
            "output_exists",
            f"{out} already exists.",
            "Choose another path, or pass overwrite=true to replace it.",
        )
    if not out.parent.is_dir():
        raise ProjectError("invalid_output", f"The output directory {out.parent} does not exist.")
    if not os.access(out.parent, os.W_OK | os.X_OK):
        raise ProjectError("permission_denied", f"Cannot write into {out.parent}.")
    return out


def prepare(
    project_path: str,
    output: str,
    preset: str = DEFAULT_PRESET,
    *,
    overwrite: bool = False,
    allow_missing_media: bool = False,
    env: Mapping[str, str] | None = None,
) -> RenderPlan:
    """Validate everything that can be checked before starting Kdenlive."""
    doc = kdenlive_doc.load(project_path)
    environ = dict(os.environ if env is None else env)
    kdenlive_exe = locate("kdenlive", environ)
    if kdenlive_exe is None:
        raise ProjectError(
            "dependency_missing",
            "Kdenlive is not available.",
            "Run diagnose_environment, or set KDENLIVE_MCP_KDENLIVE.",
        )
    if not supports_cli_render(kdenlive_exe, environ):
        raise ProjectError(
            "unsupported_kdenlive",
            f"{kdenlive_exe} does not support command-line rendering (--render/--render-preset).",
            "This option exists in Kdenlive 26.08 but not in 23.08 (Ubuntu 24.04). Install a "
            "newer Kdenlive (for example the AppImage) and set KDENLIVE_MCP_KDENLIVE to it.",
        )
    presets = {p.name: p for p in list_presets(environ).presets}
    chosen = presets.get(preset)
    if chosen is None:
        close = [n for n in presets if preset.lower() in n.lower()][:10]
        raise ProjectError(
            "unknown_preset",
            f"Kdenlive has no render preset named {preset!r}.",
            f"Similar presets: {close}" if close else "Call list_render_presets for valid names.",
        )
    out = _check_output(output, doc.path, overwrite=overwrite)
    report = media.media_report(doc, max_items=media.MAX_ITEMS_LIMIT)
    media_paths = {i.resolved_path for i in report.items if i.resolved_path}
    if str(out) in media_paths or str(out.resolve()) in media_paths:
        raise ProjectError("invalid_output", "The output path is a media file used by the project.")
    warnings: list[str] = []
    if report.missing:
        message = f"{len(report.missing)} media or asset file(s) are missing: {report.missing[:10]}"
        if not allow_missing_media:
            raise ProjectError(
                "missing_media",
                message,
                "Kdenlive would render anyway with gaps or placeholders. Relink the media, "
                "or pass allow_missing_media=true to render regardless.",
            )
        warnings.append(message)
    if chosen.extension and out.suffix.lower().lstrip(".") != chosen.extension.lower():
        warnings.append(
            f"Preset {chosen.name!r} normally writes .{chosen.extension} files; "
            f"the output is named {out.name}."
        )
    summary = project.summarize(doc)
    active = next((s for s in summary.sequences if s.active), None)
    temp = out.with_name(f".{out.name}.kdenlive-mcp-{uuid.uuid4().hex[:8]}.partial{out.suffix}")
    return RenderPlan(
        project_path=doc.path,
        project_sha256=doc.info.sha256,
        output=out,
        preset=chosen,
        overwrite=overwrite,
        kdenlive=kdenlive_exe,
        expected_seconds=active.duration_seconds if active else None,
        sequence=active.name if active else None,
        temp_output=temp,
        warnings=warnings,
    )


# ---------------------------------------------------------------- execution ----------
class RenderRun:
    """One Kdenlive render process plus its private configuration directory."""

    def __init__(self, plan: RenderPlan, env: Mapping[str, str] | None = None) -> None:
        self.plan = plan
        self.workdir = Path(tempfile.mkdtemp(prefix="kdenlive-mcp-render-"))
        environ = dict(os.environ if env is None else env)
        environ.update(
            {
                "QT_QPA_PLATFORM": "offscreen",
                "XDG_CONFIG_HOME": str(self.workdir / "config"),
                "XDG_CACHE_HOME": str(self.workdir / "cache"),
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.workdir / 'no-session-bus'}",
            }
        )
        environ.pop("DISPLAY", None)
        environ.pop("WAYLAND_DISPLAY", None)
        self.env = environ
        self.log_path = self.workdir / "render.log"
        self.started_at = time.monotonic()
        self.running: Running | None = None

    def start(self) -> None:
        args = [
            self.plan.kdenlive,
            "--render",
            "--render-preset",
            self.plan.preset.name,
            str(self.plan.project_path),
            str(self.plan.temp_output),
        ]
        self.running = start(args, log_path=str(self.log_path), env=self.env, cwd=str(self.workdir))

    def log_tail(self) -> list[str]:
        try:
            return _bounded_tail(self.log_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return []

    def cleanup(self) -> None:
        self.plan.temp_output.unlink(missing_ok=True)
        shutil.rmtree(self.workdir, ignore_errors=True)

    def finish(self, exit_code: int, *, cancelled: bool = False) -> RenderResult:
        """Validate and publish the output after the process ended (always cleans up)."""
        plan = self.plan
        if self.running is not None:
            self.running.reap_group()
        result = RenderResult(
            state=RenderState.FAILED,
            project=str(plan.project_path),
            project_sha256=plan.project_sha256,
            sequence=plan.sequence,
            output=str(plan.output),
            preset=plan.preset.name,
            exit_code=exit_code,
            elapsed_seconds=round(time.monotonic() - self.started_at, 3),
            process_completed=exit_code == 0 and not cancelled,
            log_tail=self.log_tail(),
            warnings=list(plan.warnings),
            notes=[
                "Rendered from the project file on disk; unsaved changes in a running Kdenlive "
                "are not included."
            ],
        )
        try:
            if cancelled:
                result.state, result.error = (
                    RenderState.CANCELLED,
                    "Render cancelled; partial output removed.",
                )
                return result
            if exit_code != 0:
                result.error = f"Kdenlive exited with status {exit_code}."
                return result
            temp = plan.temp_output
            if not temp.is_file() or temp.stat().st_size == 0:
                result.error = "Kdenlive reported success but produced no output file."
                return result
            result.validation = validate_output(temp, plan.expected_seconds)
            if not result.validation.ok:
                result.error = "The produced file failed validation: " + "; ".join(
                    result.validation.issues
                )
                return result
            if plan.output.exists() and not plan.overwrite:
                result.error = (
                    f"{plan.output} appeared during the render; the result was not moved."
                )
                return result
            temp.replace(plan.output)  # atomic within the same directory
            result.state = RenderState.SUCCEEDED
            return result
        finally:
            self.cleanup()


def validate_output(path: Path, expected_seconds: float | None) -> OutputValidation:
    info = media.probe_file(path)
    if not info.ok:
        return OutputValidation(ok=False, issues=[info.error or "ffprobe failed"])
    videos = sum(s.type == "video" for s in info.streams)
    audios = sum(s.type == "audio" for s in info.streams)
    issues = []
    if videos + audios == 0:
        issues.append("no audio or video stream")
    duration = info.duration_seconds
    if expected_seconds and duration is not None:
        tolerance = max(0.25, expected_seconds * 0.02)
        if abs(duration - expected_seconds) > tolerance:
            issues.append(
                f"duration {duration:.3f}s differs from the timeline ({expected_seconds:.3f}s)"
            )
    elif duration is None:
        issues.append("unknown duration")
    return OutputValidation(
        ok=not issues,
        format=info.format,
        duration_seconds=duration,
        expected_duration_seconds=expected_seconds,
        video_streams=videos,
        audio_streams=audios,
        issues=issues,
    )


def render(
    plan: RenderPlan, timeout: float = DEFAULT_TIMEOUT, env: Mapping[str, str] | None = None
) -> RenderResult:
    """Run a prepared render synchronously (bounded by `timeout` seconds)."""
    run = RenderRun(plan, env)
    try:
        run.start()
    except OSError as exc:
        run.cleanup()
        raise ProjectError(
            "render_failed", f"Cannot start Kdenlive: {exc.strerror or exc}."
        ) from exc
    assert run.running is not None  # noqa: S101 - set by start()
    try:
        code = run.running.proc.wait(timeout=timeout)
    except Exception:  # timeout or interruption: stop the whole process group
        run.running.terminate()
        result = run.finish(-1, cancelled=True)
        result.state, result.error = (
            RenderState.FAILED,
            f"Render exceeded {timeout:g}s and was stopped.",
        )
        return result
    return run.finish(code)


__all__ = [
    "DEFAULT_PRESET",
    "Preset",
    "PresetList",
    "RenderPlan",
    "RenderResult",
    "RenderRun",
    "RenderState",
    "list_presets",
    "prepare",
    "render",
    "validate_output",
]
