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

import json
import logging
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from mcp_kdenlive import policy
from mcp_kdenlive.process import ProcessTimeout, run

log = logging.getLogger(__name__)

SERVICE_PREFIX = "org.kde.kdenlive"
WINDOW_OBJECT = "/kdenlive/MainWindow_1"
QWIDGET = "org.qtproject.Qt.QWidget"
BUS_TIMEOUT = 2.0
TITLE = re.compile(r"^(?P<name>.*?)(?: \[\*\])?/ [^/]*$")


@dataclass(frozen=True)
class Window:
    """A Kdenlive main window seen on the session bus."""

    service: str
    pid: int | None
    project: str | None  # project name from the title; None for an untitled document
    modified: bool | None


def project_name(title: str) -> str | None:
    """The project name in a Kdenlive window title, or None (untitled, unknown format)."""
    match = TITLE.match(title.strip())
    name = match.group("name").strip() if match else ""
    return name if name and name != "Untitled" else None


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
        if args and Path(args[0].decode(errors="replace")).name.startswith("kdenlive"):
            found[int(entry.name)] = [a.decode(errors="replace") for a in args[1:] if a]
    return found


def _bus_env(env: Mapping[str, str]) -> dict[str, str]:
    # Renders get no session bus (docs/security.md); only this read-only query does.
    extra = {k: env[k] for k in ("DBUS_SESSION_BUS_ADDRESS",) if k in env}
    return policy.child_env(env, **extra)


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
