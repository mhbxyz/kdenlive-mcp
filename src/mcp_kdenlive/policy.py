"""Local trust boundaries: allowed filesystem roots, write targets, child-process environment.

Threat model: see docs/security.md. The MCP client (and the model driving it) is trusted to
use the tools, but tool arguments and project contents are treated as untrusted data: they may
come from a prompt-injected model or a hostile project file.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mcp_kdenlive.errors import ProjectError

ALLOWED_ROOTS_ENV = "KDENLIVE_MCP_ALLOWED_ROOTS"

# Variables passed to Kdenlive, melt and ffprobe. Everything else (tokens, credentials of other
# tools, proxies…) is dropped. Prefix entries end with "_".
CHILD_ENV_ALLOW = (
    "HOME",
    "PATH",
    "USER",
    "LOGNAME",
    "LANG",
    "LANGUAGE",
    "TZ",
    "TMPDIR",
    "LD_LIBRARY_PATH",
    "FREI0R_PATH",
    "LADSPA_PATH",
    "LV2_PATH",
    "FONTCONFIG_FILE",
    "FONTCONFIG_PATH",
    "XDG_DATA_HOME",
    "XDG_DATA_DIRS",
    "XDG_CONFIG_HOME",
    "XDG_CONFIG_DIRS",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
    "LC_",
    "MLT_",
    "QT_",
    "KDE_",
    "LIBVA_",
    "VDPAU_",
    "MESA_",
    "KDENLIVE_MCP_",
)


UNRESTRICTED = "unrestricted"


ScopeSource = Literal["default", "environment", "unrestricted"]


@dataclass(frozen=True)
class RootsConfig:
    """Effective filesystem scope and where it comes from."""

    roots: list[Path] | None  # None: unrestricted
    source: ScopeSource

    @property
    def missing(self) -> list[Path]:
        return [r for r in self.roots or [] if not r.is_dir()]

    def describe(self) -> str:
        """One line for people: the roots and their origin."""
        if self.roots is None:
            return f"unrestricted ({ALLOWED_ROOTS_ENV}={UNRESTRICTED})"
        origin = "default: videos directory" if self.source == "default" else ALLOWED_ROOTS_ENV
        text = f"{os.pathsep.join(str(r) for r in self.roots)} ({origin})"
        if self.missing:
            text += f"; does not exist: {', '.join(str(r) for r in self.missing)}"
        return text


def videos_dir(env: Mapping[str, str] | None = None) -> Path:
    """The user's videos directory, as xdg-user-dirs defines it, else ~/Videos."""
    source = os.environ if env is None else env
    home = Path(source.get("HOME") or Path.home())
    config_home = Path(source.get("XDG_CONFIG_HOME") or home / ".config")
    try:
        lines = (config_home / "user-dirs.dirs").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    for line in lines:
        key, _, value = line.strip().partition("=")
        if key != "XDG_VIDEOS_DIR":
            continue
        value = value.strip().strip('"')
        if value.startswith("$HOME"):
            value = str(home) + value[len("$HOME") :]
        path = Path(value)
        # Relative values are invalid, and a directory set to $HOME itself is disabled
        # (xdg-user-dirs conventions): both fall back to ~/Videos.
        if path.is_absolute() and path.resolve() != home.resolve():
            return path
    return home / "Videos"


def roots_config(env: Mapping[str, str] | None = None) -> RootsConfig:
    """Parse KDENLIVE_MCP_ALLOWED_ROOTS: unset → videos directory, 'unrestricted', or a list.

    Enforcement always reads the server's own environment (`env=None`); a mapping is only
    passed to describe another environment (diagnostics, tests).
    """
    raw = (os.environ if env is None else env).get(ALLOWED_ROOTS_ENV, "").strip()
    if raw == UNRESTRICTED:
        return RootsConfig(None, "unrestricted")
    roots = []
    for part in raw.split(os.pathsep):
        if not part:
            continue
        path = Path(part).expanduser()
        if not path.is_absolute():
            raise ProjectError(
                "invalid_configuration",
                f"{ALLOWED_ROOTS_ENV} entries must be absolute paths: {part!r}.",
            )
        roots.append(path.resolve())
    if roots:
        return RootsConfig(roots, "environment")
    return RootsConfig([videos_dir(env).resolve()], "default")


def _scope_hint(config: RootsConfig) -> str:
    return (
        f"Allowed roots: {config.describe()}. To allow other folders, set {ALLOWED_ROOTS_ENV}"
        " in the MCP client's server configuration to absolute directories separated by"
        f" '{os.pathsep}' (or to '{UNRESTRICTED}', not recommended)."
    )


def check_allowed(path: Path, purpose: str) -> Path:
    """Refuse `path` if it resolves (symlinks included) outside the existing allowed roots.

    The scope comes from the server's own environment, never from an environment prepared
    for a child process.
    """
    config = roots_config()
    if config.roots is None:
        return path
    real = path.resolve()
    existing = [r for r in config.roots if r.is_dir()]
    if any(real == root or root in real.parents for root in existing):
        return path
    raise ProjectError(
        "path_not_allowed",
        f"{purpose}: {path} is outside the allowed roots"
        + (f" (it resolves to {real})" if real != path else "")
        + ".",
        _scope_hint(config),
    )


def check_write_target(path: Path, purpose: str) -> Path:
    """A file the server is about to create or replace: allowed root, no symlink, regular file."""
    check_allowed(path.parent, purpose)
    if path.is_symlink():
        raise ProjectError(
            "invalid_output",
            f"{purpose}: {path} is a symbolic link; refusing to write through it.",
            "Give the real path of the file instead.",
        )
    if path.exists() and not stat.S_ISREG(path.stat().st_mode):
        raise ProjectError("invalid_output", f"{purpose}: {path} exists and is not a regular file.")
    return path


def child_env(env: Mapping[str, str] | None = None, **extra: str) -> dict[str, str]:
    """Allow-listed environment for external programs, plus explicit overrides."""
    source = os.environ if env is None else env
    allowed = {
        key: value
        for key, value in source.items()
        if key in CHILD_ENV_ALLOW
        or any(key.startswith(prefix) for prefix in CHILD_ENV_ALLOW if prefix.endswith("_"))
    }
    allowed.update(extra)
    return allowed
