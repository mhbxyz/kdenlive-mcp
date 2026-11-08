# kdenlive-mcp

<!-- mcp-name: io.github.mhbxyz/kdenlive-mcp -->

Local-first, client-agnostic [MCP](https://modelcontextprotocol.io) server for
[Kdenlive](https://kdenlive.org) on Linux.

> **Status: early development.** Available tools: environment diagnostics, read-only
> project/timeline/media inspection and local rendering ([docs/tools.md](docs/tools.md)).
> Editing is tracked in the [roadmap](https://github.com/mhbxyz/kdenlive-mcp/issues/1).

The server talks MCP over **stdio** only. It never opens a network listener. Protocol
messages go to stdout and diagnostics go to stderr.

## Requirements

- Linux
- Python 3.11 or newer
- Kdenlive, MLT (`melt`) and FFmpeg (`ffprobe`), installed separately from your distribution.
  The Python package does not install them.

## Install

The PyPI distribution is **`mcp-kdenlive`** (not yet published). The PyPI name `kdenlive-mcp` belongs
to an unrelated project, so `pipx install kdenlive-mcp` installs something else. Once released:

```sh
pipx install mcp-kdenlive
```

See [docs/packaging.md](docs/packaging.md) for virtual environments, upgrades and artifact checks.

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
| `KDENLIVE_MCP_ALLOWED_ROOTS` | `:`-separated absolute directories; every read and write must stay inside them (**recommended**, see [docs/security.md](docs/security.md)) |
| `KDENLIVE_MCP_MAX_RENDERS` | Concurrent background renders (default 1, max 4) |

Flatpak and Snap builds of Kdenlive do not expose `kdenlive`, `melt` or `ffprobe` on `PATH`.
Point the variables above to executables or wrapper scripts.

## Start the server

```sh
uv run --directory /path/to/kdenlive-mcp kdenlive-mcp     # same as: kdenlive-mcp serve
```

Tested clients (Claude Code, Codex CLI, OpenCode, Grok Build CLI) and copyable
configurations are listed in [docs/clients.md](docs/clients.md). A typical configuration:

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

- [Tools](docs/tools.md)
- [Clients](docs/clients.md)
- [Compatibility](docs/compatibility.md)
- [Versioning](docs/versioning.md)
- [Security](docs/security.md)
- [Packaging](docs/packaging.md)
- [Changelog](CHANGELOG.md)
- [Research](docs/research/integration-surfaces.md)
