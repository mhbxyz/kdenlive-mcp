# Security model

This document describes the local trust boundaries of `kdenlive-mcp` (#27). For reporting
vulnerabilities, see [SECURITY.md](../SECURITY.md).

## Threat model

| Party | Trust |
|---|---|
| The local user running the server | Trusted. The server has exactly this user's permissions. |
| The MCP client and the model driving it | Trusted to call tools, **but** a model can be manipulated (prompt injection through web pages, documents, media metadata…). Tool arguments are therefore validated as untrusted input. |
| Project files (`.kdenlive`), media files, their names and metadata | **Untrusted data.** A project may come from someone else and be hostile. |
| Kdenlive, MLT `melt`, FFmpeg `ffprobe` | Trusted executables, chosen on `PATH` or via `KDENLIVE_MCP_*`. Whoever controls those paths controls what runs. |
| The network | Not used. stdio only. No listener, and no URL is ever fetched by the server or on its behalf. |

In scope:

- reading or writing files the user did not intend;
- running other commands;
- destroying or corrupting projects;
- network access triggered by project content;
- resource exhaustion;
- leaking secrets to child processes or logs.

Out of scope: a malicious local user, compromised Kdenlive, MLT or FFmpeg binaries, and
vulnerabilities inside those programs' decoders (FFmpeg parses media; keep it updated).

## Filesystem

- **Absolute paths only.** Relative paths are refused; the server's working directory is meaningless to clients.
- **Allowed roots.** `KDENLIVE_MCP_ALLOWED_ROOTS` (`:`-separated absolute directories) restricts every path the tools read or write:
  - projects;
  - media given as arguments;
  - media probed or rendered from a project;
  - render outputs and project destinations.

  Paths are checked **after resolving symlinks and `..`**, so neither a traversal nor a symlink inside a root can escape it.

  | Value | Effective roots (`diagnose` → `filesystem.source`) |
  |---|---|
  | unset or empty | the user's **videos directory**: `XDG_VIDEOS_DIR` from `$XDG_CONFIG_HOME/user-dirs.dirs`, else `~/Videos` (`default`). If it does not exist, every path is refused with a hint. |
  | `/a:/b` | exactly these directories (`environment`) |
  | `unrestricted` | no restriction beyond the user's permissions (`unrestricted`, not recommended) |

  Until 0.1 the unset variable meant unrestricted. 0.2 changed the default (#60) because a manipulated model could otherwise reach any file of the user, and changing a default after 1.0 would be a breaking change. Refusing to start without configuration was rejected: MCP clients report a failed server with little detail, and the registry's `uvx mcp-kdenlive` would fail on the first run.
- **Write targets.**
  - Nothing is written through a symlink, or over anything but a regular file (FIFOs, devices and directories are refused).
  - Project destinations must end in `.kdenlive`.
  - Render outputs must have a media extension, so a render can never replace a script or a dotfile.
  - Existing files are replaced only with `overwrite=true` and are backed up first. The source project is replaced only with `overwrite=true` plus its `expected_sha256`.
- **Atomic writes and conflicts.** Writes go to a temporary file in the destination directory, are fsynced, then linked or renamed into place. The source is re-hashed just before writing. Failed writes leave no partial file, and inputs are never opened for writing.
- **Open projects.** Kdenlive overwrites a project file when it saves and does not notice external changes (demonstrated, see `docs/tools.md`). Writing over a project that appears open in Kdenlive is refused (`[open_in_kdenlive]`). Detection (#62, `presence.py`, evidence in [research/open-projects.md](research/open-projects.md)):

  | Signal | Catches | Misses |
  |---|---|---|
  | `/proc/*/cmdline` of processes named `kdenlive` | `kdenlive project.kdenlive` | projects opened from the *Open* dialog or the recent-files list |
  | Kdenlive autosave lock (`stalefiles/kdenlive/*.lock`, live pid) | projects with unsaved changes | unmodified projects |
  | Main window title over the session D-Bus (`busctl --user get-property org.kde.kdenlive-* /kdenlive/MainWindow_1 org.qtproject.Qt.QWidget windowTitle`) | any project currently shown, even unmodified (verified in Kdenlive 26.08.2) | the folder: the title has only the file name, so a project with the same name elsewhere is also refused |

  The D-Bus query is **read-only** (`org.freedesktop.DBus.Properties.Get` on two properties; no Kdenlive method is ever called) and runs with a 2 s timeout. It is the only child process given `DBUS_SESSION_BUS_ADDRESS`. Renders are explicitly cut off from the bus; melt and ffprobe receive `XDG_RUNTIME_DIR`, through which a D-Bus client could find the default session bus, but they are not D-Bus clients. Every running Kdenlive process must be matched, by pid, to a window with a readable title; when one cannot be checked (no `busctl`, no session bus, a busy or unknown window), the write goes through with a warning asking the user to close or reload the project. `create_project` with `overwrite` follows the same rule.

  Signals investigated and rejected: Kdenlive's recent-files and recent-folders entries are only written when Kdenlive exits; the per-project cache folder (`~/.cache/kdenlive/<documentid>`) is created when the project is closed and not touched when it is reopened; Kdenlive keeps no file descriptor on the project; `windowFilePath` is empty.

### TOCTOU limits

- **Conflict check versus rename:** a small window remains between the hash check and the rename. There is no portable advisory locking shared with Kdenlive.
- **Symlink checks versus use:** they happen at validation time. A local process able to swap path components between validation and use can still race the server. That requires the same user's privileges, which is out of scope.

## Untrusted project content

- **XML:** parsed with expat, with no DTD, no entity declarations and no external references (billion laughs and XXE are refused). Limits: 64 MiB, 256 levels of nesting, 2 million elements.
- **Media references** are resolved like Kdenlive does and checked against the allowed roots before any program reads them.
- **URLs** in projects (`http://`, `rtmp://`…) are reported as `remote` and never fetched. Rendering a project that references remote media is refused (`[remote_media]`): otherwise Kdenlive or MLT would download it.
- **Bounded output:** timelines (`max_clips`), media lists (`max_items`), ffprobe calls (50 per listing, 15 s each) and render job queues (20 queued, at most 4 concurrent) are all limited.

## Subprocesses

- **No shell.** Programs are started from argument lists, with stdin closed, in their own process group, with a timeout. Hostile file names (`-i x`, `;rm`, `$(…)`, newlines, quotes) are passed as plain arguments:
  - **melt:** with an explicit `avformat:` prefix, so names starting with `-` or containing `=` are not options or properties;
  - **ffprobe:** with a `file:` prefix and `-protocol_whitelist file`.
- **Cleanup:** on timeout or cancellation the whole process group is killed. Renders run with a private `TMPDIR`, configuration and cache, without a session D-Bus, and never talk to a running Kdenlive.
- **Environment allow-list.** Child processes receive only the variables they need:
  - basics: `HOME`, `PATH`, `USER`, `LANG`/`LC_*`, `TZ`, `TMPDIR`;
  - XDG directories;
  - plugin paths and library settings: `MLT_*`, `QT_*`, `KDE_*`, `FREI0R_PATH`, `LADSPA_PATH`, `LV2_PATH`, `LD_LIBRARY_PATH`, `LIBVA_*`, `VDPAU_*`, `MESA_*`, fontconfig;
  - `KDENLIVE_MCP_*`;
  - only for the read-only Kdenlive window check (#62): `DBUS_SESSION_BUS_ADDRESS`.

  Tokens and credentials of other tools in the server's environment are not inherited.
- **Executable trust.** The programs found on `PATH` (or set via `KDENLIVE_MCP_*`) are trusted. Do not point these variables at untrusted files.

## Logs

- **stdout** carries only MCP protocol messages; diagnostics go to stderr.
- **Render log tails** returned to clients come from Kdenlive's own output (bounded, noise filtered). They contain paths, not environment variables.
- **Tests** must never print whole environments (see CONTRIBUTING).

## Known limitations

- **Inside the allowed roots,** every project and media file is in reach of a manipulated model (with the safeguards above: copies by default, backups, conflict checks). Backup rotation keeps the first backup of a project (its state before the server's first edit) and the 4 most recent, so a series of writes cannot flush the original; it only ever removes backups the server wrote since 0.2. Keep the roots narrow, and avoid `unrestricted`.
- **Open-project detection** relies on the window title for projects opened from Kdenlive's dialogs: it needs `busctl` and the session bus, and it matches by file name only (see *Open projects*).
- **Decoders:** media decoding vulnerabilities in FFmpeg or MLT are inherited; keep them updated.
