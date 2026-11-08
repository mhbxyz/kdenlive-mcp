"""Which Kdenlive instances are running, and which project each one shows (#62).

Kdenlive overwrites its project file when it saves and does not notice external changes, so
the server must not replace a project that Kdenlive has open. Signals, from strongest:

- a Kdenlive process started with the file (`kdenlive project.kdenlive`);
- an autosave lock of unsaved changes (`stalefiles/kdenlive`);
- the **main window title**, read over the session D-Bus: Kdenlive exports its main window's
  Qt properties, and `windowTitle` is "<project name> [*]/ <profile>". It names the project even
  when it was opened from the *Open* dialog without changes (verified with Kdenlive 26.08.2 in
  the headless GUI lab). The title has the project's name, not its folder.

Everything here is read-only: it lists bus names and reads two properties
(`org.freedesktop.DBus.Properties.Get`). It never calls a Kdenlive method.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from mcp_kdenlive import policy
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.process import ProcessTimeout, run

log = logging.getLogger(__name__)

SERVICE_PREFIX = "org.kde.kdenlive"
WINDOW_OBJECT = "/kdenlive/MainWindow_1"
QWIDGET = "org.qtproject.Qt.QWidget"
BUS_TIMEOUT = 2.0
# Qt keeps the "[*]" modified placeholder in windowTitle; the profile may contain "/".
TITLE_SEPARATOR = " [*]/ "


class KdenliveUsage(BaseModel):
    open: bool | None = Field(
        description="True: Kdenlive appears to have the file open (see evidence). False: every "
        "running Kdenlive was checked, or none runs. None: a running Kdenlive could not be checked."
    )
    evidence: list[str]


@dataclass(frozen=True)
class Window:
    """A Kdenlive main window seen on the session bus."""

    service: str
    pid: int | None
    project: str | None  # project name from the title ("Untitled" too); None if unreadable
    modified: bool | None


def project_name(title: str) -> str | None:
    """The project name in a Kdenlive window title, or None for an unknown format."""
    name, separator, _profile = title.partition(TITLE_SEPARATOR)
    return name.strip() or None if separator else None


def kdenlive_processes() -> dict[int, list[str]]:
    """Running processes whose program is named kdenlive: {pid: arguments}."""
    found: dict[int, list[str]] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return found
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        # The Kdenlive program itself, not helpers such as kdenlive_render, nor command-line
        # renders (`kdenlive --render`), which never hold a project open for editing (#92).
        if args and Path(args[0].decode(errors="replace")).name == "kdenlive":
            rest = [a.decode(errors="replace") for a in args[1:] if a]
            if "--render" not in rest:
                found[int(entry.name)] = rest
    return found


def _bus_env(env: Mapping[str, str]) -> dict[str, str]:
    # The only child process given the session bus address (docs/security.md).
    address = env.get("DBUS_SESSION_BUS_ADDRESS")
    return policy.child_env(env, **({"DBUS_SESSION_BUS_ADDRESS": address} if address else {}))


def _busctl(busctl: str, env: Mapping[str, str], *args: str) -> object | None:
    try:
        done = run(
            [busctl, "--user", "--json=short", "--no-pager", *args],
            timeout=BUS_TIMEOUT,
            env=_bus_env(env),
        )
    except (OSError, ProcessTimeout) as exc:
        log.debug("busctl %s failed: %s", args[0], exc)
        return None
    if done.returncode != 0:
        return None
    try:
        data: object = json.loads(done.stdout)
    except json.JSONDecodeError:
        return None
    return data


def _property(busctl: str, env: Mapping[str, str], service: str, name: str) -> object | None:
    data = _busctl(busctl, env, "get-property", service, WINDOW_OBJECT, QWIDGET, name)
    return data.get("data") if isinstance(data, dict) else None


def windows(env: Mapping[str, str] | None = None) -> list[Window] | None:
    """Kdenlive windows on the session bus; None when the bus cannot be read."""
    source = os.environ if env is None else env
    busctl = shutil.which("busctl", path=source.get("PATH"))
    if busctl is None:
        return None
    listing = _busctl(busctl, source, "list")
    if not isinstance(listing, list):
        return None
    found = []
    for entry in listing:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name.startswith(SERVICE_PREFIX):
            continue
        title = _property(busctl, source, name, "windowTitle")
        modified = _property(busctl, source, name, "windowModified")
        pid = entry.get("pid")
        found.append(
            Window(
                service=name,
                pid=pid if isinstance(pid, int) else None,
                project=project_name(title) if isinstance(title, str) else None,
                modified=modified if isinstance(modified, bool) else None,
            )
        )
    return found


def _stale_lock_owners(target: str) -> list[int]:
    """Live pids holding a Kdenlive autosave lock (unsaved changes) for `target`."""
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    stale = data_home / "stalefiles" / "kdenlive"
    encoded = urllib.parse.quote(target, safe="")
    owners = []
    if stale.is_dir():
        for lock in stale.glob("*.lock"):
            if encoded not in lock.name and encoded.replace("%2F", "%2f") not in lock.name:
                continue
            try:
                pid = int(lock.read_text(errors="replace").split()[0])
                os.kill(pid, 0)
                owners.append(pid)
            except (OSError, ValueError, IndexError):
                continue
    return owners


def kdenlive_usage(path: Path) -> KdenliveUsage:
    """Best-effort detection of a Kdenlive instance showing `path` (three signals, see above)."""
    evidence: list[str] = []
    target = str(path.resolve())
    processes = kdenlive_processes()
    for pid, args in processes.items():
        for arg in args:
            with contextlib.suppress(OSError, ValueError):
                if str(Path(arg).resolve()) == target:
                    evidence.append(f"Kdenlive process {pid} was started with this file.")
    for pid in _stale_lock_owners(target):
        evidence.append(f"Kdenlive (pid {pid}) holds an autosave of unsaved changes.")
    shown = windows() if processes or evidence else []
    for window in shown or []:
        if window.project == path.stem:
            evidence.append(
                f"Kdenlive{f' (pid {window.pid})' if window.pid else ''} shows a project named "
                f"{window.project!r} in its window"
                + (" with unsaved changes" if window.modified else "")
                + " (the title has no folder: it may be another file with the same name)."
            )
    if evidence:
        return KdenliveUsage(open=True, evidence=evidence)
    # Ruled out only if every Kdenlive process has a window on the bus with a readable title.
    readable = {w.pid for w in shown or [] if w.project is not None}
    return KdenliveUsage(open=False if set(processes) <= readable else None, evidence=[])


RUNNING_WARNING = (
    "Kdenlive is running and it could not be checked whether it has {name} open. If it does, "
    "close the project there without saving, or reload it (File > Revert), otherwise Kdenlive "
    "will overwrite this change the next time it saves."
)


def check_not_open(dest: Path) -> list[str]:
    """Refuse to replace a file Kdenlive shows; return a warning when it cannot be ruled out."""
    if not dest.exists():
        return []
    usage = kdenlive_usage(dest)
    if usage.open:
        raise ProjectError(
            "open_in_kdenlive",
            f"{dest} appears to be open in Kdenlive: " + "; ".join(usage.evidence),
            "Close it in Kdenlive (or save to another file) and retry.",
        )
    return [RUNNING_WARNING.format(name=dest.name)] if usage.open is None else []
