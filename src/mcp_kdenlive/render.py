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

import html
import os
import re
import shutil
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from mcp_kdenlive import flatpak, kdenlive_doc, media, policy, project, safexml
from mcp_kdenlive.environment import check_visible, locate, work_dir
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.process import ProcessTimeout, Running, run, start
from mcp_kdenlive.timecode import FrameRate

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
ENGINES = {"auto", "kdenlive", "melt"}
MELT_PROGRESS = re.compile(r"Current Frame:\s*(\d+), percentage:\s*(\d+)")
MELT_NOTE = (
    "Rendered with MLT's melt, not Kdenlive's own render path: proxy clips, render zones and "
    "Kdenlive-specific render options are ignored, and the preset's defaults are used for "
    "quality, bitrate and speed."
)


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
    if exe and flatpak.is_wrapper(exe) and (app_files := flatpak.files_dir(environ)):
        candidates.append(app_files / "share" / "kdenlive" / "export" / "profiles.xml")
    elif exe:  # relocatable installs (AppImage, /opt): <prefix>/bin/kdenlive -> <prefix>/share
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


def preset_definitions(paths: list[Path]) -> dict[str, dict[str, str]]:
    """Raw attributes of each preset (later files override earlier ones, like Kdenlive)."""
    found: dict[str, dict[str, str]] = {}
    for path in paths:
        try:
            root, _ = safexml.parse_file(path, max_bytes=8 * 1024 * 1024)
        except (OSError, ValueError, safexml.ParseError):
            continue
        for prof in root.iter("profile"):
            if prof.get("name"):
                found[prof.get("name", "")] = dict(prof.attrib)
    return found


PLACEHOLDER = re.compile(r"%([a-z_]+)")
SAFE_KEY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:+-]*")
# Properties that would change what melt reads or where it writes.
FORBIDDEN_KEYS = {"target", "resource", "consumer", "producer", "mlt_service", "real_time"}
IMAGE_SEQUENCE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp",
    "tga",
    "tif",
    "tiff",
    "exr",
    "dpx",
    "bmp",
}
SUFFIX = re.compile(r"(\S*?)\+'([^']*)'")


def _default(attrs: dict[str, str], name: str) -> str | None:
    """A preset default: `default<name>`, else the middle of `<name>s` (a 'min,max' range)."""
    if attrs.get(f"default{name}"):
        return attrs[f"default{name}"]
    plural = name[:-1] + "ies" if name.endswith("y") else name + "s"
    values = [v for v in attrs.get(plural, "").split(",") if v.strip()]
    try:
        numbers = sorted(int(v) for v in values)
    except ValueError:
        return values[0] if values else None
    return str((numbers[0] + numbers[-1]) // 2) if numbers else None


def melt_consumer_args(attrs: dict[str, str], rate: FrameRate) -> list[str]:
    """Kdenlive preset arguments as melt avformat consumer properties, with Kdenlive's defaults.

    Mirrors Kdenlive's substitution: %quality, %audioquality, %bitrate and %audiobitrate take
    the preset defaults; `+'k'` suffixes are joined; %dv_standard is pal at 25 fps, ntsc
    otherwise; the default speed option is appended.
    """
    values = {
        "quality": _default(attrs, "quality"),
        "audioquality": _default(attrs, "audioquality"),
        "bitrate": _default(attrs, "bitrate"),
        "audiobitrate": _default(attrs, "audiobitrate"),
        "dv_standard": "pal" if rate.num == 25 * rate.den else "ntsc",
    }

    def fill(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        if value is None:
            raise ProjectError(
                "unknown_preset",
                f"Preset {attrs.get('name')!r} uses %{match.group(1)}, which the melt engine "
                "cannot fill.",
                "Use engine='kdenlive' with a Kdenlive that supports --render, or another preset.",
            )
        return value

    if (attrs.get("extension") or "").lower() in IMAGE_SEQUENCE_EXTENSIONS:
        raise ProjectError(
            "unknown_preset",
            f"Preset {attrs.get('name')!r} writes an image sequence; the melt engine does not.",
            "Use engine='kdenlive' (Kdenlive 24.02+) or a video preset.",
        )
    text = PLACEHOLDER.sub(fill, attrs.get("args", ""))
    text = SUFFIX.sub(lambda m: m.group(1) + m.group(2), text)
    args = text.split()
    for token in args:
        key, sep, value = token.partition("=")
        if not sep or not SAFE_KEY.fullmatch(key) or key in FORBIDDEN_KEYS or "://" in value:
            raise ProjectError(
                "unknown_preset",
                f"Preset {attrs.get('name')!r} has an argument the melt engine refuses: {token!r}.",
                "Only key=value encoder settings are passed to melt; outputs, inputs and URLs "
                "are not.",
            )
    speeds = [s for s in attrs.get("speeds", "").split(";")]
    if speeds and any(speeds):
        index = attrs.get("defaultspeedindex")
        # Without a default, Kdenlive uses the middle entry (observed with 26.08.2).
        position = int(index) if index and index.isdigit() else len(speeds) // 2
        args += speeds[min(position, len(speeds) - 1)].split()
    return args


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
    engine: str = Field(
        default="kdenlive",
        description="'kdenlive' (kdenlive --render) or 'melt' (MLT directly, see notes).",
    )
    exit_code: int | None = None
    elapsed_seconds: float | None = None
    process_completed: bool = Field(description="The render process exited with status 0.")
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
        environ = policy.child_env(env, QT_QPA_PLATFORM="offscreen")
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
    engine: str = "kdenlive"
    melt: str | None = None
    consumer_args: list[str] = field(default_factory=list)
    effective_root: Path | None = None


def _bounded_tail(text: str) -> list[str]:
    lines = [ln for ln in text.splitlines() if ln.strip() and not any(n in ln for n in _NOISE)]
    return [ln[:300] for ln in lines[-LOG_TAIL_LINES:]]


# Render outputs must look like media files: a render can never replace a script or a dotfile.
OUTPUT_EXTENSIONS = {
    "mp4",
    "m4v",
    "mkv",
    "webm",
    "mov",
    "avi",
    "mpg",
    "mpeg",
    "ts",
    "m2ts",
    "mxf",
    "ogv",
    "ogg",
    "oga",
    "opus",
    "mp3",
    "wav",
    "flac",
    "aac",
    "ac3",
    "m4a",
    "alac",
    "gif",
    "png",
    "jpg",
    "jpeg",
    "webp",
    "dv",
    "vob",
    "3gp",
}


def _check_output(output: str, project_path: Path, *, overwrite: bool) -> Path:
    out = Path(output).expanduser()
    if not output or not out.is_absolute():
        raise ProjectError("invalid_output", f"{output!r} is not an absolute output path.")
    if out.suffix.lower().lstrip(".") not in OUTPUT_EXTENSIONS:
        raise ProjectError(
            "invalid_output",
            f"{out.name}: render outputs need a media file "
            f"extension ({', '.join(sorted(OUTPUT_EXTENSIONS))}).",
        )
    policy.check_write_target(out, "Rendering")
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


def _choose_engine(
    engine: str,
    kdenlive_exe: str | None,
    melt_exe: str | None,
    has_cli: bool,
    subtitles: bool,
    warnings: list[str],
) -> str:
    """'auto' picks Kdenlive when it can render from the command line and the project has no
    subtitles (Kdenlive 26.08.2 crashes on them), melt otherwise."""
    if engine == "auto":
        engine = "kdenlive" if has_cli and not subtitles else "melt"
    if engine == "kdenlive":
        if kdenlive_exe is None:
            raise ProjectError(
                "dependency_missing",
                "Kdenlive is not available.",
                "Run diagnose_environment, or set KDENLIVE_MCP_KDENLIVE.",
            )
        if not has_cli:
            raise ProjectError(
                "unsupported_kdenlive",
                f"{kdenlive_exe} does not support command-line rendering (--render). It exists "
                "from Kdenlive 24.02.",
                "Use engine='melt' (or 'auto'), or install a newer Kdenlive and set "
                "KDENLIVE_MCP_KDENLIVE to it.",
            )
        if subtitles:
            warnings.append(
                "This project has subtitles: Kdenlive 26.08.2's command-line render crashed "
                "(SIGSEGV) on such projects in our tests; engine='melt' renders them."
            )
        return engine
    if melt_exe is None:
        raise ProjectError(
            "dependency_missing",
            "melt (MLT) is not available for the melt engine.",
            "Install MLT, or set KDENLIVE_MCP_MELT.",
        )
    warnings.append(MELT_NOTE)
    return engine


def prepare(
    project_path: str,
    output: str,
    preset: str = DEFAULT_PRESET,
    *,
    overwrite: bool = False,
    allow_missing_media: bool = False,
    env: Mapping[str, str] | None = None,
    engine: str = "auto",
) -> RenderPlan:
    """Validate everything that can be checked before starting the render."""
    if engine not in ENGINES:
        raise ProjectError(
            "invalid_argument", f"engine must be one of {sorted(ENGINES)}, not {engine!r}."
        )
    doc = kdenlive_doc.load(project_path)
    environ = dict(os.environ if env is None else env)
    kdenlive_exe = locate("kdenlive", environ)
    melt_exe = locate("melt", environ)
    has_cli = kdenlive_exe is not None and supports_cli_render(kdenlive_exe, environ)
    report = media.media_report(doc, max_items=media.MAX_ITEMS_LIMIT)
    warnings: list[str] = []
    subtitles = any(a.service == "avfilter.subtitles" for a in report.asset_files)
    engine = _choose_engine(engine, kdenlive_exe, melt_exe, has_cli, subtitles, warnings)
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
    media_paths = {i.resolved_path for i in report.items if i.resolved_path}
    if str(out) in media_paths or str(out.resolve()) in media_paths:
        raise ProjectError("invalid_output", "The output path is a media file used by the project.")
    remote = [i.reference for i in report.items if i.status is media.MediaStatus.REMOTE]
    if remote:
        raise ProjectError(
            "remote_media",
            f"The project references remote media: {remote[:5]}.",
            "Rendering would make Kdenlive/MLT fetch them over the network; "
            "download them and relink the project first.",
        )
    for item in report.items:
        if item.resolved_path and item.status is media.MediaStatus.OK:
            policy.check_allowed(Path(item.resolved_path), "Rendering reads media")
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
    program = kdenlive_exe if engine == "kdenlive" else melt_exe
    check_visible(doc.path, program, "Rendering a project")
    check_visible(out.parent, program, "Render output")
    for item in report.items:
        if item.resolved_path and item.status is media.MediaStatus.OK:
            check_visible(Path(item.resolved_path), program, "Rendering reads media")
    temp = out.with_name(f".{out.name}.kdenlive-mcp-{uuid.uuid4().hex[:8]}.partial{out.suffix}")
    consumer: list[str] = []
    if engine == "melt":
        definitions = preset_definitions([path for path, _ in preset_files(environ)])
        consumer = melt_consumer_args(definitions.get(chosen.name, {}), doc.rate)
    return RenderPlan(
        project_path=doc.path,
        project_sha256=doc.info.sha256,
        output=out,
        preset=chosen,
        overwrite=overwrite,
        kdenlive=kdenlive_exe or "",
        expected_seconds=active.duration_seconds if active else None,
        sequence=active.name if active else None,
        temp_output=temp,
        warnings=warnings,
        engine=engine,
        melt=melt_exe,
        consumer_args=consumer,
        effective_root=Path(media._Resolver(doc).effective),
    )


# ---------------------------------------------------------------- execution ----------
class RenderRun:
    """One Kdenlive render process plus its private configuration directory."""

    def __init__(self, plan: RenderPlan, env: Mapping[str, str] | None = None) -> None:
        self.plan = plan
        # In the server's cache, not /tmp: a Flatpak Kdenlive cannot see /tmp (#65).
        self.workdir = work_dir("kdenlive-mcp-render-", env)
        environ = policy.child_env(env)
        environ.update(
            {
                "QT_QPA_PLATFORM": "offscreen",
                "XDG_CONFIG_HOME": str(self.workdir / "config"),
                "XDG_CACHE_HOME": str(self.workdir / "cache"),
                # kdenlive_render looks for running Kdenlive instances (org.kde.kdenlive-* sockets)
                # in the temp dir to report progress to them; a private TMPDIR keeps renders from
                # talking to the user's open Kdenlive, and holds the temporary MLT scene list.
                "TMPDIR": str(self.workdir / "tmp"),
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.workdir / 'no-session-bus'}",
                # Flatpak launchers forward the private folders above into the sandbox.
                "KDENLIVE_MCP_PRIVATE_DIRS": "1",
            }
        )
        environ.pop("DISPLAY", None)
        environ.pop("WAYLAND_DISPLAY", None)
        self.env = environ
        (self.workdir / "tmp").mkdir()
        self.log_path = self.workdir / "render.log"
        self.started_at = time.monotonic()
        self.running: Running | None = None

    def start(self) -> None:
        if self.plan.engine == "melt":
            scene = self._scene_with_root()
            args = [
                self.plan.melt or "melt",
                str(scene),
                "-progress",
                "-consumer",
                f"avformat:{self.plan.temp_output}",
                *self.plan.consumer_args,
                # Kdenlive's own consumer defaults (captured from its render scene).
                "rescale=bilinear",
                "deinterlacer=onefield",
                "real_time=-1",
            ]
            self.running = start(
                args, log_path=str(self.log_path), env=self.env, cwd=str(self.workdir)
            )
            return
        args = [
            self.plan.kdenlive,
            "--render",
            "--render-preset",
            self.plan.preset.name,
            str(self.plan.project_path),
            str(self.plan.temp_output),
        ]
        self.running = start(args, log_path=str(self.log_path), env=self.env, cwd=str(self.workdir))

    def _scene_with_root(self) -> Path:
        """A copy of the project whose `root` is the folder Kdenlive would use.

        Kdenlive (and this server) treat a stored root that no longer exists as a moved project
        and resolve relative media against the project's folder; melt does not, and renders a
        blank, silent picture (found in the #42 review).
        """
        text = self.plan.project_path.read_text(encoding="utf-8")
        root = html.escape(str(self.plan.effective_root), quote=True)
        text, count = re.subn(r'(<mlt\b[^>]*?\sroot=)"[^"]*"', rf'\1"{root}"', text, count=1)
        if not count:
            text = re.sub(r"<mlt\b", f'<mlt root="{root}"', text, count=1)
        scene = self.workdir / "scene.kdenlive"
        scene.write_text(text, encoding="utf-8")
        return scene

    def log_tail(self) -> list[str]:
        try:
            return _bounded_tail(self.log_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return []

    def progress(self) -> tuple[int, int] | None:
        """(percent, frame) from melt's -progress output or kdenlive_render's own log."""
        if self.plan.engine == "melt":
            try:
                text = self.log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
            found = MELT_PROGRESS.findall(text)
            if not found:
                return None
            frame, percent = found[-1]
            return int(percent), int(frame)
        log = self.plan.temp_output.with_name(self.plan.temp_output.name + ".log")
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            parts = line.split("\t")
            if len(parts) == 3 and all(p.strip().isdigit() for p in parts):
                percent, frame = int(parts[2]), int(parts[1])
                if 0 <= percent <= 100:
                    return percent, frame
        return None

    def cleanup(self) -> None:
        self.plan.temp_output.unlink(missing_ok=True)
        self.plan.temp_output.with_name(self.plan.temp_output.name + ".log").unlink(missing_ok=True)
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
            engine=plan.engine,
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
                program = "melt" if plan.engine == "melt" else "Kdenlive"
                result.error = f"{program} exited with status {exit_code}."
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
            "render_failed",
            f"Cannot start {'melt' if plan.engine == 'melt' else 'Kdenlive'}: "
            f"{exc.strerror or exc}.",
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
