"""Kdenlive installed as a Flatpak (#65).

Flathub's ``org.kde.kdenlive`` (26.08.3 observed) is not on PATH: Flatpak exports a launcher in
``<installation>/exports/bin``. The app bundles melt and FFmpeg, run with
``flatpak run --command=<tool> org.kde.kdenlive``. Its permissions include ``filesystems=host``,
which shows the home and most host folders **but not /tmp, /var/tmp, /usr, /etc…**: a project
there makes ``kdenlive --render`` exit 0 without writing anything. ``flatpak run`` also replaces
TMPDIR and XDG_CONFIG_HOME with the app's own.

When no host program is found, the server writes small launchers in its private cache
(``$XDG_CACHE_HOME/kdenlive-mcp/flatpak-bin``, mode 0700). They pass every argument through
unchanged (``"$@"``, no shell interpretation) and forward the private TMPDIR and XDG folders
only when a render isolates itself (``KDENLIVE_MCP_PRIVATE_DIRS=1``).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path

APP = "org.kde.kdenlive"
TOOLS = ("kdenlive", "melt", "ffprobe", "ffmpeg")
FLATPAK = shutil.which("flatpak") or "/usr/bin/flatpak"
# Host folders the sandbox does not see even with filesystems=host (Flatpak documentation:
# reserved paths), checked after resolving symlinks.
HIDDEN = (
    "/tmp",  # noqa: S108 - a path the sandbox cannot see, not a temporary file
    "/var/tmp",  # noqa: S108
    "/usr", "/lib", "/lib32", "/lib64", "/bin", "/sbin", "/etc", "/proc", "/sys", "/dev", "/run",
    "/app", "/boot",
)  # fmt: skip
DISABLE_ENV = "KDENLIVE_MCP_FLATPAK"  # "off" disables Flatpak detection

WRAPPER = """#!/bin/sh
# Written by kdenlive-mcp (#65): runs {tool} from the Flatpak {app} ({where} installation).
# Arguments are passed through unchanged. Private folders are forwarded only for isolated renders.
if [ "${{KDENLIVE_MCP_PRIVATE_DIRS:-}}" = 1 ]; then
  exec {flatpak} run --{where} --command={tool} --env=TMPDIR="$TMPDIR" \\
    --env=XDG_CONFIG_HOME="$XDG_CONFIG_HOME" --env=XDG_CACHE_HOME="$XDG_CACHE_HOME" \\
    {app} "$@"
fi
exec {flatpak} run --{where} --command={tool} {app} "$@"
"""


def _home(env: Mapping[str, str]) -> Path:
    return Path(env.get("HOME") or Path.home())


def cache_dir(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    base = Path(source.get("XDG_CACHE_HOME") or _home(source) / ".cache")
    return base / "kdenlive-mcp"


def installation(env: Mapping[str, str] | None = None) -> str | None:
    """'user' or 'system' when a Flatpak Kdenlive is installed, else None."""
    source = os.environ if env is None else env
    if source.get(DISABLE_ENV, "").strip().lower() in ("off", "0", "no", "false"):
        return None
    data_home = Path(source.get("XDG_DATA_HOME") or _home(source) / ".local" / "share")
    if (data_home / "flatpak" / "exports" / "bin" / APP).exists():
        return "user"
    if (Path("/var/lib/flatpak/exports/bin") / APP).exists():
        return "system"
    return None


def wrapper(tool: str, env: Mapping[str, str] | None = None) -> str | None:
    """Path of the launcher for `tool` inside the Flatpak, written if needed; None if absent."""
    where = installation(env)
    if where is None or tool not in TOOLS:
        return None
    directory = cache_dir(env) / "flatpak-bin"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / tool
    text = WRAPPER.format(tool=tool, app=APP, where=where, flatpak=FLATPAK)
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.chmod(0o700)
        tmp.replace(path)
    return str(path)


def is_wrapper(program: str | None) -> bool:
    return bool(program) and Path(str(program)).parent.name == "flatpak-bin"


def hidden(path: Path) -> bool:
    """True if a Flatpak app with filesystems=host cannot see `path`."""
    real = str(path.resolve())
    return any(real == h or real.startswith(h + "/") for h in HIDDEN)
