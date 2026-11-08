# kdenlive-mcp

<!-- mcp-name: io.github.mhbxyz/kdenlive-mcp -->

Local-first, client-agnostic [MCP](https://modelcontextprotocol.io) server for
[Kdenlive](https://kdenlive.org) on Linux.

Inspect, edit and render Kdenlive projects from an AI assistant, locally on Linux: timelines,
media diagnostics, safe project copies, clip edits and rendering with Kdenlive presets.

```sh
pipx install mcp-kdenlive      # NOT "kdenlive-mcp": that PyPI name is an unrelated project
kdenlive-mcp diagnose
```

**Start here: [docs/user-guide.md](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/user-guide.md)** (install, client setup, safety, upgrade,
troubleshooting). The available tools are listed in [docs/tools.md](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/tools.md), and the
planned work in the [roadmap](https://github.com/mhbxyz/kdenlive-mcp/issues/1).

## Requirements

- Linux
- Python 3.11 or newer
- Kdenlive, MLT (`melt`) and FFmpeg (`ffprobe`), installed separately from your distribution.
  The Python package does not install them.

## Install

```sh
pipx install mcp-kdenlive
```

The PyPI distribution is **`mcp-kdenlive`**. The PyPI name `kdenlive-mcp` belongs to an
unrelated project, so `pipx install kdenlive-mcp` installs something else. See
[docs/packaging.md](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/packaging.md) for virtual environments, upgrades and artifact checks.

### From source

```sh
git clone https://github.com/mhbxyz/kdenlive-mcp.git
cd kdenlive-mcp
uv sync            # or: python -m venv .venv && .venv/bin/pip install -e .
uv run kdenlive-mcp --version
```

## Check your environment

```sh
uv run kdenlive-mcp diagnose          # human-readable, exit status 1 if something required is missing
uv run kdenlive-mcp diagnose --json   # machine-readable
```

The same report is available to MCP clients through the `diagnose_environment` tool.

Programs are looked up on `PATH`. Override their location with these environment variables:

| Variable | Program |
|---|---|
| `KDENLIVE_MCP_KDENLIVE` | `kdenlive` (for example an AppImage path) |
| `KDENLIVE_MCP_MELT` | `melt` |
| `KDENLIVE_MCP_FFPROBE` | `ffprobe` |
| `KDENLIVE_MCP_FFMPEG` | `ffmpeg` (optional) |
| `KDENLIVE_MCP_PROBE_TIMEOUT` | Seconds allowed for each version probe (default 10) |
| `KDENLIVE_MCP_ALLOWED_ROOTS` | `:`-separated absolute directories; every read and write must stay inside them (**recommended**, see [docs/security.md](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/security.md)) |
| `KDENLIVE_MCP_MAX_RENDERS` | Concurrent background renders (default 1, max 4) |

Flatpak and Snap builds of Kdenlive do not expose `kdenlive`, `melt` or `ffprobe` on `PATH`.
Point the variables above to executables or wrapper scripts.

## Start the server

```sh
uv run --directory /path/to/kdenlive-mcp kdenlive-mcp     # same as: kdenlive-mcp serve
```

Tested clients (Claude Code, Codex CLI, OpenCode, Grok Build CLI) and copyable
configurations are listed in [docs/clients.md](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/clients.md). A typical configuration:

```json
{
  "mcpServers": {
    "kdenlive": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/kdenlive-mcp", "kdenlive-mcp"],
      "env": { "KDENLIVE_MCP_KDENLIVE": "/usr/bin/kdenlive" }
    }
  }
}
```

MCP clients often start servers with a reduced environment. If tools are reported missing
from a client but not from your shell, pass `PATH` or the `KDENLIVE_MCP_*` variables
explicitly.

To try the server with the official MCP Inspector:

```sh
npx @modelcontextprotocol/inspector --cli uv run --directory "$PWD" kdenlive-mcp --method tools/list
```

## Development

```sh
uv sync
uv run pytest            # unit + protocol tests; integration tests skip when tools are missing
```

Architecture: domain modules (`environment.py`, `process.py`) do not depend on MCP.
`server.py` is the only MCP transport layer.

Documentation:

- [User guide](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/user-guide.md)
- [Releasing (maintainers)](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/releasing.md)
- [Contributing](https://github.com/mhbxyz/kdenlive-mcp/blob/main/CONTRIBUTING.md)
- [Tools](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/tools.md)
- [Clients](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/clients.md)
- [Compatibility](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/compatibility.md)
- [Versioning](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/versioning.md)
- [Security model](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/security.md)
- [Security policy (reporting vulnerabilities)](https://github.com/mhbxyz/kdenlive-mcp/blob/main/SECURITY.md)
- [Incident response](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/incident-response.md)
- [Packaging](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/packaging.md)
- [MCP Registry](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/registry.md)
- [Changelog](https://github.com/mhbxyz/kdenlive-mcp/blob/main/CHANGELOG.md)
- [Research](https://github.com/mhbxyz/kdenlive-mcp/blob/main/docs/research/integration-surfaces.md)
