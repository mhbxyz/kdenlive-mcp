"""Kdenlive installed as a Flatpak (#65).

Flathub's ``org.kde.kdenlive`` (26.08.3 observed) is not on PATH: Flatpak exports a launcher in
``<installation>/exports/bin``. The app bundles melt and FFmpeg, run with
``flatpak run --command=<tool> org.kde.kdenlive``. With its default ``filesystems=host``
permission it sees the home and most host folders **but not /tmp, /usr, /etc, /var…**: a project
there makes ``kdenlive --render`` exit 0 without writing anything. ``flatpak run`` also replaces
TMPDIR and XDG_CONFIG_HOME with the app's own, and the app keeps its user data (custom render
presets) in ``~/.var/app/org.kde.kdenlive/data``.

When no host program is found, the server writes small launchers in its private cache
(``$XDG_CACHE_HOME/kdenlive-mcp/flatpak-bin``, folders 0700). They pass every argument through
unchanged (``"$@"``), drop the host's ``MLT_*`` variables (host paths mean nothing inside), and
forward the private TMPDIR and XDG folders only for isolated renders
(``KDENLIVE_MCP_PRIVATE_DIRS=1``, set by the server itself).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

APP = "org.kde.kdenlive"
TOOLS = ("kdenlive", "melt", "ffprobe", "ffmpeg")
FLATPAK = shutil.which("flatpak") or "/usr/bin/flatpak"
SYSTEM = Path("/var/lib/flatpak")
DISABLE_ENV = "KDENLIVE_MCP_FLATPAK"  # "off" (or 0, no, false) disables Flatpak detection
OFF = ("off", "0", "no", "false")
# Host folders a sandbox with filesystems=host does not see (Flatpak's reserved paths), with
# the exceptions it does expose. Checked after resolving symlinks.
HIDDEN = (
    "/tmp",  # noqa: S108 - a path the sandbox cannot see, not a temporary file
    "/var", "/usr", "/lib", "/lib32", "/lib64", "/bin", "/sbin", "/etc", "/proc", "/sys",
    "/dev", "/run", "/app", "/boot",
)  # fmt: skip
VISIBLE = ("/run/media", "/var/home", "/var/mnt", "/var/media")
XDG_DIRS = {
    "xdg-desktop": "DESKTOP", "xdg-documents": "DOCUMENTS", "xdg-download": "DOWNLOAD",
    "xdg-music": "MUSIC", "xdg-pictures": "PICTURES", "xdg-videos": "VIDEOS",
}  # fmt: skip

WRAPPER = """#!/bin/sh
# Written by kdenlive-mcp (#65): runs {tool} from the Flatpak {app} ({where} installation).
# Arguments are passed through unchanged. Host MLT_* settings are dropped; private folders are
# forwarded only for isolated renders. flatpak finds its helper (bwrap) on PATH.
for v in $(env | sed -n 's/^\\(MLT_[A-Za-z0-9_]*\\)=.*/\\1/p'); do unset "$v"; done
PATH="${{PATH:+$PATH:}}"{flatpak_dir}":/usr/bin:/bin"; export PATH
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


def private_dir(path: Path, env: Mapping[str, str] | None = None) -> Path:
    """Create `path` (inside the cache) with every folder from the cache root at mode 0700."""
    root = cache_dir(env)
    path.mkdir(parents=True, exist_ok=True)
    for folder in (path, *path.parents):
        folder.chmod(0o700)
        if folder == root:
            break
    return path


def installation(env: Mapping[str, str] | None = None) -> str | None:
    """'user' or 'system' when a Flatpak Kdenlive is installed, else None."""
    source = os.environ if env is None else env
    if source.get(DISABLE_ENV, "").strip().lower() in OFF:
        return None
    data_home = Path(source.get("XDG_DATA_HOME") or _home(source) / ".local" / "share")
    if (data_home / "flatpak" / "exports" / "bin" / APP).exists():
        return "user"
    if (SYSTEM / "exports" / "bin" / APP).exists():
        return "system"
    return None


def files_dir(env: Mapping[str, str] | None = None) -> Path | None:
    """The app's /app folder as seen from the host (MLT profiles, render presets)."""
    source = os.environ if env is None else env
    where = installation(source)
    if where == "user":
        data_home = Path(source.get("XDG_DATA_HOME") or _home(source) / ".local" / "share")
        base = data_home / "flatpak"
    elif where == "system":
        base = SYSTEM
    else:
        return None
    path = base / "app" / APP / "current" / "active" / "files"
    return path if path.is_dir() else None


def app_data_dir(env: Mapping[str, str] | None = None) -> Path:
    """The app's XDG_DATA_HOME (user render presets live there, not in ~/.local/share)."""
    return _home(os.environ if env is None else env) / ".var" / "app" / APP / "data"


def wrapper(tool: str, env: Mapping[str, str] | None = None) -> str | None:
    """Path of the launcher for `tool` inside the Flatpak, written if needed; None if absent."""
    where = installation(env)
    if where is None or tool not in TOOLS:
        return None
    directory = private_dir(cache_dir(env) / "flatpak-bin", env)
    path = directory / tool
    text = WRAPPER.format(
        tool=tool,
        app=APP,
        where=where,
        flatpak=shlex.quote(FLATPAK),
        flatpak_dir=shlex.quote(str(Path(FLATPAK).parent)),
    )
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        # A unique temporary name, then an atomic rename: concurrent servers never see a
        # partial launcher.
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{tool}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            Path(tmp).chmod(0o700)
            Path(tmp).replace(path)
        finally:
            Path(tmp).unlink(missing_ok=True)
    return str(path)


def is_wrapper(program: str | None, env: Mapping[str, str] | None = None) -> bool:
    if not program:
        return False
    return Path(program).parent == cache_dir(env) / "flatpak-bin"


@lru_cache(maxsize=4)
def _permissions(where: str) -> str:
    """The app's effective `filesystems` (user overrides included), e.g. 'host;xdg-run/…'."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed argument list
            [FLATPAK, "info", f"--{where}", "--show-permissions", APP],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "host"
    for line in done.stdout.splitlines():
        if line.startswith("filesystems="):
            return line.removeprefix("filesystems=")
    return ""


def permissions(env: Mapping[str, str] | None = None) -> str:
    where = installation(env)
    return _permissions(where) if where else "host"


def _xdg_dir(name: str, home: Path) -> Path:
    try:
        lines = (home / ".config" / "user-dirs.dirs").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        key, _, value = line.partition("=")
        if key.strip() == f"XDG_{name}_DIR":
            return Path(value.strip().strip('"').replace("$HOME", str(home)))
    return home / name.capitalize()


def _under(text: str, root: str | Path) -> bool:
    base = str(root).rstrip("/")
    return text == base or text.startswith(base + "/")


def _granted(text: str, grant: str, home: Path) -> bool:
    if grant in ("home", "~"):
        return _under(text, home)
    if grant in XDG_DIRS:
        return _under(text, _xdg_dir(XDG_DIRS[grant], home))
    if grant.startswith("~/"):
        return _under(text, home / grant[2:])
    return grant.startswith("/") and _under(text, grant)


def hidden(path: Path, permissions: str | None = None) -> bool:
    """True if the Flatpak Kdenlive cannot see `path` with its `filesystems` permissions."""
    text = str(Path(path).resolve())
    grants = [g.split(":")[0] for g in (permissions or "").split(";") if g]
    if permissions is None:
        grants = ["host"]
    if "host" in grants or "host-os" in grants:
        if any(_under(text, v) for v in VISIBLE):
            return False
        return any(_under(text, h) for h in HIDDEN)
    home = Path.home()
    return not any(_granted(text, g, home) for g in grants)
