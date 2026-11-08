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


@dataclass(frozen=True)
class RootsConfig:
    """Effective filesystem scope and where it comes from."""

    roots: list[Path] | None  # None: unrestricted
    source: str  # "default" (videos directory), "environment" or "unrestricted"


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
        # A directory set to $HOME itself is disabled (xdg-user-dirs convention).
        if path.is_absolute() and path.resolve() != home.resolve():
            return path
    return home / "Videos"


def roots_config(env: Mapping[str, str] | None = None) -> RootsConfig:
    """Parse KDENLIVE_MCP_ALLOWED_ROOTS: unset → videos directory, 'unrestricted', or a list."""
    raw = (os.environ if env is None else env).get(ALLOWED_ROOTS_ENV, "").strip()
    if raw == UNRESTRICTED:
        return RootsConfig(None, UNRESTRICTED)
    if not raw:
        return RootsConfig([videos_dir(env).resolve()], "default")
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
    if not roots:
        return RootsConfig([videos_dir(env).resolve()], "default")
    return RootsConfig(roots, "environment")


def allowed_roots(env: Mapping[str, str] | None = None) -> list[Path] | None:
    """Effective roots (resolved), or None when explicitly unrestricted."""
    return roots_config(env).roots


def _scope_hint(config: RootsConfig) -> str:
    roots = [str(r) for r in config.roots or []]
    origin = (
        "the default (your videos directory)"
        if config.source == "default"
        else f"{ALLOWED_ROOTS_ENV}"
    )
    hint = f"Allowed roots, from {origin}: {roots}."
    missing = [r for r in roots if not Path(r).is_dir()]
    if missing:
        hint += f" {missing} does not exist."
    return (
        hint + f" To allow other folders, set {ALLOWED_ROOTS_ENV} in the MCP client's server"
        f" configuration to absolute directories separated by '{os.pathsep}'"
        f" (or to '{UNRESTRICTED}', not recommended)."
    )


def check_allowed(path: Path, purpose: str, env: Mapping[str, str] | None = None) -> Path:
    """Refuse `path` if it resolves (symlinks included) outside the allowed roots."""
    config = roots_config(env)
    if config.roots is None:
        return path
    real = path.resolve()
    if any(real == root or root in real.parents for root in config.roots):
        return path
    raise ProjectError(
        "path_not_allowed",
        f"{purpose}: {path} is outside the allowed roots"
        + (f" (it resolves to {real})" if real != path else "")
        + ".",
        _scope_hint(config),
    )


def check_write_target(path: Path, purpose: str, env: Mapping[str, str] | None = None) -> Path:
    """A file the server is about to create or replace: allowed root, no symlink, regular file."""
    check_allowed(path.parent, purpose, env)
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
