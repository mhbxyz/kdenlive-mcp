# User guide

`kdenlive-mcp` lets an AI assistant that speaks MCP inspect, edit and render your Kdenlive
projects **on your own Linux machine**. It runs locally over stdio; nothing is sent anywhere.

> **Release status.** The first public release is **0.1.0** (PyPI `mcp-kdenlive`). Features
> listed in the roadmap (#1) but not in [docs/tools.md](tools.md) are **planned**, not available.

## 1. Install the system programs

The Python package does **not** install Kdenlive, MLT or FFmpeg. Install them from your
distribution:

| Distribution | Command | Status |
|---|---|---|
| Arch Linux / CachyOS | `sudo pacman -S kdenlive mlt ffmpeg` | tested (Kdenlive 26.08) |
| Ubuntu 24.04 / Debian | `sudo apt install kdenlive melt ffmpeg` | tested in CI. Kdenlive 23.08 cannot render from the command line, so `render_project` refuses there. Use a newer Kdenlive (AppImage) for rendering. |
| Fedora | `sudo dnf install kdenlive mlt ffmpeg` (or `ffmpeg-free`) | untested |
| Kdenlive AppImage | download from kdenlive.org, `chmod +x`, then set `KDENLIVE_MCP_KDENLIVE=/path/to/kdenlive.AppImage` | untested |
| Flatpak / Snap | the programs are not on `PATH`: write wrapper scripts and point the `KDENLIVE_MCP_*` variables at them | untested |

Supported versions and their status: [docs/compatibility.md](compatibility.md).

## 2. Install the server

You need Python ≥ 3.11 and [pipx](https://pipx.pypa.io/):

```sh
pipx install mcp-kdenlive
kdenlive-mcp --version
kdenlive-mcp diagnose          # checks Kdenlive, melt, ffprobe; exit status 1 if one is missing
```

⚠️ The package is **`mcp-kdenlive`**. `pipx install kdenlive-mcp` installs an **unrelated
project** from another author. Our command is `kdenlive-mcp`, also available as `mcp-kdenlive`.

Without pipx:

```sh
python3 -m venv ~/.venvs/kdenlive-mcp && ~/.venvs/kdenlive-mcp/bin/pip install mcp-kdenlive
```

Or with uv: `uvx mcp-kdenlive --version`.

## 3. Connect your MCP client

The server command is `kdenlive-mcp` (stdio). Use its absolute path, from `command -v kdenlive-mcp`
(usually `~/.local/bin/kdenlive-mcp` with pipx), because clients often do not inherit your shell's
`PATH`.

```json
{ "mcpServers": { "kdenlive": {
    "command": "/home/you/.local/bin/kdenlive-mcp",
    "env": { "KDENLIVE_MCP_ALLOWED_ROOTS": "/home/you/Videos" } } } }
```

Client-specific instructions (Claude Code, Codex CLI, OpenCode, Grok), the tested versions and
the smoke test with the MCP Inspector are in [docs/clients.md](clients.md). The server is also
listed in the official MCP Registry as `io.github.mhbxyz/kdenlive-mcp`
([docs/registry.md](registry.md)).

**Which folders the server can reach.** Since 0.2, without configuration the server only reads
and writes inside your **videos directory** (`XDG_VIDEOS_DIR` from `~/.config/user-dirs.dirs`,
usually `~/Videos`, `~/Vidéos` on a French desktop). If your projects or media live elsewhere,
set `KDENLIVE_MCP_ALLOWED_ROOTS` to those folders, separated by `:`. `kdenlive-mcp diagnose`
prints the effective folders (line `files`), and the server logs them on stderr at startup.
`KDENLIVE_MCP_ALLOWED_ROOTS=unrestricted` restores the 0.1 behaviour; it is not recommended,
because a model manipulated by a web page or a document could then reach any of your files
([docs/security.md](security.md)).

| Variable | Purpose |
|---|---|
| `KDENLIVE_MCP_ALLOWED_ROOTS` | `:`-separated folders the server may read and write. Unset: your videos directory only. `unrestricted`: no limit (not recommended) |
| `KDENLIVE_MCP_KDENLIVE`, `KDENLIVE_MCP_MELT`, `KDENLIVE_MCP_FFPROBE`, `KDENLIVE_MCP_FFMPEG` | executables that are not on `PATH` |
| `KDENLIVE_MCP_PROBE_TIMEOUT` | seconds allowed per version probe (default 10) |
| `KDENLIVE_MCP_MAX_RENDERS` | concurrent background renders (default 1, max 4) |

## 4. Working safely with Kdenlive

- **Close the project in Kdenlive first.** Kdenlive does not notice files changed on disk and overwrites them when it saves; an external change to an open project is lost.
- **Copies by default.** Editing tools write to the `destination` you give. Overwriting the original needs `overwrite=true` plus the `expected_sha256` of the version that was read. Replaced files are backed up as `<name>.backup-<timestamp>.kdenlive`.
- **Disk state only.** Rendering uses the project saved on disk, not unsaved changes in a running Kdenlive.

## 5. Upgrade, check, uninstall

```sh
pipx upgrade mcp-kdenlive
kdenlive-mcp --version
pipx uninstall mcp-kdenlive
```

What changed between versions, and any migration notes for breaking changes:
[CHANGELOG.md](../CHANGELOG.md). Versioning rules: [docs/versioning.md](versioning.md).

## 6. Troubleshooting

| Symptom | What to do |
|---|---|
| `diagnose` says a program is `missing` only when run from the client | The client starts the server with a reduced `PATH`: set the `KDENLIVE_MCP_*` variables in the client's `env`. |
| `kdenlive` reported `failed`, or a crash with no display | The probe already uses `QT_QPA_PLATFORM=offscreen`. Run `QT_QPA_PLATFORM=offscreen kdenlive --version` yourself to see the error. |
| `[unsupported_kdenlive]` when rendering | Your Kdenlive (for example 23.08) has no command-line rendering: install a newer one and point `KDENLIVE_MCP_KDENLIVE` at it. |
| `[missing_media]` / `list_project_media` shows `missing` | Media moved or deleted. Check `resolved_path`; relinking tools are planned (#17). |
| Render `failed` with codec errors in `log_tail` | Your FFmpeg/MLT build lacks that codec; pick another preset (`list_render_presets`). |
| Render of a project with subtitles crashes | Known Kdenlive 26.08.2 command-line bug (#16); rendering from Kdenlive's GUI works. |
| `[path_not_allowed]` | The path is outside the allowed folders: your videos directory by default, or `KDENLIVE_MCP_ALLOWED_ROOTS` (symlinks are resolved first). Add the folder to `KDENLIVE_MCP_ALLOWED_ROOTS` in the client's server configuration. |
| `[open_in_kdenlive]` | Close the project in Kdenlive, or write to another file. |
| Nothing on stdout, MCP client hangs | Run `kdenlive-mcp --log-level DEBUG` from the client's config. Diagnostics go to stderr, which most clients show in their MCP logs. |

What the server does **not** do: it does not bundle or install Kdenlive, has no remote or
network mode (#22), and does not control a Kdenlive window that is already open (#20).

Report bugs in GitHub issues. Report security problems privately: [SECURITY.md](../SECURITY.md).
