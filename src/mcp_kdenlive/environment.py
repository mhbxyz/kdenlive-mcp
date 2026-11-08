"""Detection of the external programs the server relies on (Kdenlive, MLT, FFmpeg).

This module is independent of MCP: it only returns plain data models. It never modifies
anything and never invokes a shell; every probe is an argument list with a timeout.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from mcp_kdenlive import __version__, flatpak, policy
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.policy import child_env
from mcp_kdenlive.process import ProcessTimeout, run

DEFAULT_PROBE_TIMEOUT = 10.0
TIMEOUT_ENV_VAR = "KDENLIVE_MCP_PROBE_TIMEOUT"
_MAX_DETAIL = 500


class ToolStatus(StrEnum):
    """Outcome of probing one external program."""

    AVAILABLE = "available"
    """Found, executed, and a version was recognised."""
    VERSION_UNKNOWN = "version_unknown"
    """Found and executed successfully, but the version output was not recognised."""
    MISSING = "missing"
    """Not configured and not found on PATH."""
    NOT_EXECUTABLE = "not_executable"
    """A configured or found path exists but cannot be executed (or is not a file)."""
    FAILED = "failed"
    """The version probe exited with a non-zero status or could not be started."""
    TIMEOUT = "timeout"
    """The version probe did not finish within the probe timeout."""


@dataclass(frozen=True)
class ToolSpec:
    """How to find and identify one external program."""

    name: str
    candidates: tuple[str, ...]
    override_env: str
    version_args: tuple[str, ...]
    version_pattern: str
    required: bool
    purpose: str
    install_hint: str
    extra_env: tuple[tuple[str, str], ...] = ()


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="kdenlive",
        candidates=("kdenlive",),
        override_env="KDENLIVE_MCP_KDENLIVE",
        version_args=("--version",),
        version_pattern=r"kdenlive\s+(\d[\w.\-+~]*)",
        required=True,
        purpose="Rendering with Kdenlive presets and Kdenlive-specific compatibility checks.",
        install_hint=(
            "Install Kdenlive from your distribution, or set KDENLIVE_MCP_KDENLIVE to the "
            "absolute path of a Kdenlive executable (for example an AppImage)."
        ),
        # Without a display Kdenlive aborts before printing its version.
        extra_env=(("QT_QPA_PLATFORM", "offscreen"),),
    ),
    ToolSpec(
        name="melt",
        candidates=("melt", "melt-7"),
        override_env="KDENLIVE_MCP_MELT",
        version_args=("--version",),
        version_pattern=r"melt\s+(\d[\w.\-+~]*)",
        required=True,
        purpose="MLT command-line player used for MLT-level checks and fallback rendering.",
        install_hint=(
            "Install MLT (package often named 'mlt' or 'melt'), or set KDENLIVE_MCP_MELT to "
            "the absolute path of the melt executable."
        ),
    ),
    ToolSpec(
        name="ffprobe",
        candidates=("ffprobe",),
        override_env="KDENLIVE_MCP_FFPROBE",
        version_args=("-version",),
        version_pattern=r"ffprobe version\s+(\S+)",
        required=True,
        purpose="Media metadata inspection and rendered output validation.",
        install_hint=(
            "Install FFmpeg (it provides ffprobe), or set KDENLIVE_MCP_FFPROBE to the "
            "absolute path of the ffprobe executable."
        ),
    ),
    ToolSpec(
        name="ffmpeg",
        candidates=("ffmpeg",),
        override_env="KDENLIVE_MCP_FFMPEG",
        version_args=("-version",),
        version_pattern=r"ffmpeg version\s+(\S+)",
        required=False,
        purpose="Optional: not used by current tools; reported for troubleshooting.",
        install_hint="Install FFmpeg, or set KDENLIVE_MCP_FFMPEG to the ffmpeg executable.",
    ),
)


class ToolReport(BaseModel):
    """Diagnostic result for one external program."""

    name: str = Field(description="Program identifier, e.g. 'kdenlive'.")
    status: ToolStatus
    required: bool = Field(description="Whether core features need this program.")
    purpose: str
    path: str | None = Field(default=None, description="Resolved absolute path, when found.")
    source: str | None = Field(
        default=None, description="'override' (environment variable) or 'path' (PATH lookup)."
    )
    override_env: str = Field(description="Environment variable that overrides the location.")
    version: str | None = Field(default=None, description="Version reported by the program.")
    detail: str | None = Field(
        default=None, description="Bounded probe output or error explanation."
    )
    hint: str | None = Field(default=None, description="Actionable next step when not available.")


class PlatformInfo(BaseModel):
    system: str
    release: str
    machine: str
    python: str
    flatpak_sandbox: bool = Field(description="True when this server itself runs inside Flatpak.")


class FilesystemAccess(BaseModel):
    """Which folders the tools may read and write (KDENLIVE_MCP_ALLOWED_ROOTS, #60)."""

    roots: list[str] | None = Field(description="Allowed roots, resolved; null when unrestricted.")
    source: Literal["default", "environment", "unrestricted", "invalid"] = Field(
        description="'default' (the user's videos directory), 'environment' "
        "(KDENLIVE_MCP_ALLOWED_ROOTS), 'unrestricted' (explicit opt-out) or 'invalid' "
        "(see error; every path is refused)."
    )
    missing: list[str] = Field(description="Allowed roots that do not exist.")
    summary: str | None = Field(default=None, description="One-line description for people.")
    error: str | None = Field(default=None, description="Invalid configuration, if any.")


class EnvironmentReport(BaseModel):
    """Read-only report on the local environment."""

    server_version: str
    platform: PlatformInfo
    ready: bool = Field(description="True when every required program is available.")
    tools: list[ToolReport]
    filesystem: FilesystemAccess
    notes: list[str] = Field(
        default_factory=list,
        description="Limitations of this report: what was not verified.",
    )


def probe_timeout_from_env(env: Mapping[str, str]) -> float:
    """Read the probe timeout (seconds) from the environment, falling back to the default."""
    raw = env.get(TIMEOUT_ENV_VAR)
    if not raw:
        return DEFAULT_PROBE_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_PROBE_TIMEOUT
    return value if 0 < value <= 300 else DEFAULT_PROBE_TIMEOUT


def _bounded(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _MAX_DETAIL else text[:_MAX_DETAIL] + "…"


def _resolve(
    spec: ToolSpec, env: Mapping[str, str]
) -> tuple[str | None, str | None, ToolReport | None]:
    """Find the executable. Returns (path, source, early_report_if_unusable)."""
    override = env.get(spec.override_env, "").strip()
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_absolute():
            found = shutil.which(override, path=env.get("PATH"))
            if found is None:
                return (
                    None,
                    "override",
                    _report(
                        spec,
                        ToolStatus.MISSING,
                        source="override",
                        detail=f"{spec.override_env}={override!r} was not found on PATH.",
                        hint=f"Set {spec.override_env} to an absolute path of an executable file.",
                    ),
                )
            candidate = Path(found)
        if not candidate.exists():
            return (
                None,
                "override",
                _report(
                    spec,
                    ToolStatus.MISSING,
                    source="override",
                    path=str(candidate),
                    detail=f"{spec.override_env} points to a path that does not exist.",
                    hint=f"Fix or unset {spec.override_env}.",
                ),
            )
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            return (
                None,
                "override",
                _report(
                    spec,
                    ToolStatus.NOT_EXECUTABLE,
                    source="override",
                    path=str(candidate),
                    detail=f"{spec.override_env} does not point to an executable file.",
                    hint=f"Point {spec.override_env} to an executable file, or make it executable.",
                ),
            )
        return str(candidate), "override", None
    found, source = _search(spec, env)
    if found is not None:
        return found, source, None
    return (
        None,
        None,
        _report(
            spec,
            ToolStatus.MISSING,
            detail=f"None of {list(spec.candidates)} was found on PATH.",
            hint=spec.install_hint,
        ),
    )


def _search(spec: ToolSpec, env: Mapping[str, str]) -> tuple[str | None, str | None]:
    """PATH first, then a launcher into a Flatpak Kdenlive (#65)."""
    for name in spec.candidates:
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            return found, "path"
    launcher = flatpak.wrapper(spec.name, env)
    return (launcher, "flatpak") if launcher is not None else (None, None)


def _report(spec: ToolSpec, status: ToolStatus, **fields: str | None) -> ToolReport:
    return ToolReport(
        name=spec.name,
        status=status,
        required=spec.required,
        purpose=spec.purpose,
        override_env=spec.override_env,
        **fields,
    )


def probe_tool(spec: ToolSpec, env: Mapping[str, str], timeout: float) -> ToolReport:
    """Locate one program and ask it for its version, without a shell."""
    path, source, early = _resolve(spec, env)
    if early is not None:
        return early
    if path is None:  # pragma: no cover - _resolve always returns a path or a report
        raise RuntimeError(f"no path resolved for {spec.name}")
    run_env = child_env(env, **dict(spec.extra_env))
    try:
        completed = run([path, *spec.version_args], timeout=timeout, env=run_env)
    except ProcessTimeout:
        return _report(
            spec,
            ToolStatus.TIMEOUT,
            path=path,
            source=source,
            detail=f"No answer to {' '.join(spec.version_args)} within {timeout:g}s.",
            hint=f"Check that the program starts correctly, or raise {TIMEOUT_ENV_VAR}.",
        )
    except OSError as exc:
        return _report(
            spec,
            ToolStatus.FAILED,
            path=path,
            source=source,
            detail=f"Could not start the program: {exc.strerror or exc}.",
            hint=f"Check permissions and that {path} is a valid executable for this system.",
        )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        return _report(
            spec,
            ToolStatus.FAILED,
            path=path,
            source=source,
            detail=_bounded(f"Exit status {completed.returncode}. {output}"),
            hint="Run the program manually to see why it fails.",
        )
    match = re.search(spec.version_pattern, output, flags=re.IGNORECASE)
    if match is None:
        return _report(
            spec,
            ToolStatus.VERSION_UNKNOWN,
            path=path,
            source=source,
            detail=_bounded(output) or "The program printed nothing.",
            hint="The program ran but its version output was not recognised.",
        )
    return _report(spec, ToolStatus.AVAILABLE, path=path, source=source, version=match.group(1))


def work_dir(prefix: str, env: Mapping[str, str] | None = None) -> Path:
    """A private temporary folder (0700) in the server's cache, which a Flatpak Kdenlive can
    see (it cannot see /tmp). The caller removes it."""
    base = flatpak.cache_dir(env) / "tmp"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=base))


@contextmanager
def temporary_dir(prefix: str, env: Mapping[str, str] | None = None) -> Iterator[Path]:
    """work_dir() as a context manager that removes the folder afterwards."""
    path = work_dir(prefix, env)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def check_visible(path: Path, program: str | None, what: str) -> None:
    """Refuse a path that a Flatpak program (launcher) cannot see."""
    if flatpak.is_wrapper(program) and flatpak.hidden(path):
        raise ProjectError(
            "invalid_path",
            f"{what}: {path} is in a folder the Flatpak Kdenlive cannot see "
            "(/tmp, /usr, /etc and other system folders are hidden from it).",
            "Move it under your home folder (or another data folder), or install Kdenlive "
            "from your distribution.",
        )


def locate(name: str, env: Mapping[str, str] | None = None) -> str | None:
    """Path of a usable executable for tool `name` (override or PATH), without running it."""
    spec = next((s for s in TOOL_SPECS if s.name == name), None)
    if spec is None:
        raise KeyError(name)
    path, _source, problem = _resolve(spec, dict(os.environ if env is None else env))
    return None if problem is not None else path


def filesystem_access(env: Mapping[str, str]) -> FilesystemAccess:
    try:
        config = policy.roots_config(env)
    except ProjectError as err:
        return FilesystemAccess(roots=None, source="invalid", missing=[], error=err.message)
    roots = [str(r) for r in config.roots] if config.roots is not None else None
    return FilesystemAccess(
        roots=roots,
        source=config.source,
        missing=[str(r) for r in config.missing],
        summary=config.describe(),
    )


def diagnose(env: Mapping[str, str] | None = None) -> EnvironmentReport:
    """Probe every known program and return a read-only report."""
    environ = dict(os.environ if env is None else env)
    timeout = probe_timeout_from_env(environ)
    tools = [probe_tool(spec, environ, timeout) for spec in TOOL_SPECS]
    ready = all(t.status is ToolStatus.AVAILABLE for t in tools if t.required)
    notes = [
        "Availability means the program started and printed a version; it does not prove "
        "that rendering, codecs, MLT plugins or fonts work.",
        "Versions are reported as observed; no minimum supported version is enforced yet.",
    ]
    if any(t.source == "flatpak" for t in tools):
        notes.append(
            "Programs marked 'flatpak' run inside the Flatpak org.kde.kdenlive, which cannot see "
            "/tmp, /usr, /etc and other system folders: keep projects, media and render outputs "
            "under your home folder (or another data folder)."
        )
    if any(t.name == "kdenlive" and t.status is ToolStatus.MISSING for t in tools):
        notes.append(
            "Kdenlive was not found on PATH nor as a Flatpak (org.kde.kdenlive, detected "
            "automatically unless KDENLIVE_MCP_FLATPAK=off). Snap installations do not expose "
            "kdenlive, melt or ffprobe on PATH; point the KDENLIVE_MCP_* variables to executables "
            "or wrapper scripts."
        )
    return EnvironmentReport(
        server_version=__version__,
        platform=PlatformInfo(
            system=platform.system(),
            release=platform.release(),
            machine=platform.machine(),
            python=sys.version.split()[0],
            flatpak_sandbox=Path("/.flatpak-info").exists(),
        ),
        ready=ready,
        tools=tools,
        filesystem=filesystem_access(environ),
        notes=notes,
    )
