# Detecting a project open in Kdenlive

Evidence for #62. Kdenlive overwrites its project file when it saves and does not notice
external changes, so the server must not replace a project that Kdenlive shows.

Method: Kdenlive 26.08.2 in the headless GUI lab (`research/lab`, private XDG directories and
private session bus), project opened from **File > Open** with no change, then each signal
checked while Kdenlive runs (`kdgui.py` `run` command, `/proc`, the lab's XDG folders).

| Signal | Result |
|---|---|
| `/proc/<pid>/cmdline` | `kdenlive --no-welcome`: no project when opened from the dialog. Kept for `kdenlive file.kdenlive` launches. |
| `/proc/<pid>/fd` | no descriptor on the project: Kdenlive reads the file and closes it. |
| `kdenliverc` *Recent Files* / *Recent Dirs* | written when Kdenlive exits, not when a project is opened. |
| `~/.cache/kdenlive/<documentid>/` | created when the project is **closed**; not touched when it is reopened later. |
| `stalefiles/kdenlive/*.lock` | only once there are unsaved changes (kept). |
| D-Bus `org.qtproject.Qt.QWidget.windowFilePath` | empty. |
| D-Bus `org.qtproject.Qt.QWidget.windowTitle` | `Untitled [*]/ HD 1080p 25 fps` before, **`p62 [*]/ HD 720p 29.97 fps`** after opening `p62.kdenlive`. Read-only property, available as soon as the window exists. |
| D-Bus `org.qtproject.Qt.QWidget.windowModified` | `false` for an unmodified project. |

Kdenlive's service is `org.kde.kdenlive-<pid>`, the window object `/kdenlive/MainWindow_1`.
`busctl --user --json=short list` gives each name with its pid.

## Decision

- Use the window title as a third signal (`presence.py`): a running Kdenlive whose title names
  a project with the destination's file name blocks the write (`[open_in_kdenlive]`). The title
  has no folder, so a project with the same name elsewhere is also blocked: a false positive
  that only prevents replacing a file, and never loses data.
- Read-only: list names, then `org.freedesktop.DBus.Properties.Get` on two properties, with
  `busctl` and a 2 s timeout. No Kdenlive method is called.
- When a Kdenlive process runs but cannot be checked, write anyway and warn.
- Verified end to end in the lab: before opening, `open=False`; after opening `p62` from the
  dialog, `open=True` with the window evidence; another project stays `open=False`.
- Not verified: Kdenlive 23.08's title format (no GUI in CI). Flatpak Kdenlive is checked in #65.
