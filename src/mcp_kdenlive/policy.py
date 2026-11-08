"""Local trust boundaries: allowed filesystem roots, write targets, child-process environment.

Threat model: see docs/security.md. The MCP client (and the model driving it) is trusted to
use the tools, but tool arguments and project contents are treated as untrusted data: they may
come from a prompt-injected model or a hostile project file.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
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


def allowed_roots(env: Mapping[str, str] | None = None) -> list[Path] | None:
    """Configured roots (resolved), or None when unrestricted."""
    raw = (os.environ if env is None else env).get(ALLOWED_ROOTS_ENV, "").strip()
    if not raw:
        return None
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
    return roots or None


def check_allowed(path: Path, purpose: str, env: Mapping[str, str] | None = None) -> Path:
    """Refuse `path` if it resolves (symlinks included) outside the allowed roots."""
    roots = allowed_roots(env)
    if roots is None:
        return path
    real = path.resolve()
    if any(real == root or root in real.parents for root in roots):
        return path
    raise ProjectError(
        "path_not_allowed",
        f"{purpose}: {path} is outside the allowed roots"
        + (f" (it resolves to {real})" if real != path else "")
        + ".",
        f"Allowed roots ({ALLOWED_ROOTS_ENV}): {[str(r) for r in roots]}.",
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
