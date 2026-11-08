# MCP client compatibility

The server speaks standard MCP over **stdio**. It uses no vendor-specific extensions, and
every tool result carries both `structuredContent` and the same data as JSON text, for
clients that ignore structured output.

## Tested matrix

Tested on 2026-11-08, Linux (CachyOS, KDE Plasma), server `0.1.0.dev0`. Each client ran the
same headless scenario:

1. list the server's tools;
2. `inspect_project` on `tests/fixtures/kd26/kd26-ntsc-timeline.kdenlive`, then report the frame rate and duration;
3. `get_timeline`, then report the V1 clip's `start`, `end` and `source_out`;
4. `inspect_project` with a relative path, then report the error code.

Expected answers: `30000/1001`, `60 frames`, `0 / 60 / 59`, `[invalid_path]`.

| Client (product) | Version | Model used | Config method tested | Result |
|---|---|---|---|---|
| Claude Code (Anthropic CLI) | 2.1.319 | claude-opus-5-5 | `claude -p … --mcp-config mcp.json --strict-mcp-config --allowedTools mcp__kdenlive__…` | ✅ all 4 steps |
| Codex CLI (OpenAI) | codex-cli 0.165.0 | gpt-6.1-terra | `codex exec -c 'mcp_servers.kdenlive.command="…"' -c 'mcp_servers.kdenlive.args=[]'` | ✅ all 4 steps; the error is shown as a failed tool call |
| OpenCode | 1.19.4 | user's default (not recorded) | project `opencode.json` with an `mcp.kdenlive` `local` entry, `opencode run` | ✅ all 4 steps |
| Grok Build CLI (xAI) | 1.0.77 stable | grok-4.6-build | `grok mcp add -s user …`, then `grok -p … --allow "mcp__<name>__*"` | ✅ all 4 steps; handshake on protocol 2025-11-25 |
| MCP Inspector CLI | 2.14.0 (npx) | — | `npx @modelcontextprotocol/inspector --cli <command> --method tools/list` | ✅ discovery and schemas |
| Official MCP Python SDK `Client` | mcp 2.3.0 | — | automated tests (in-process and real stdio subprocess) | ✅ in CI |
| Raw JSON-RPC client using the 2025-06-18 `initialize` handshake | — | — | automated test (`tests/test_server.py`) | ✅ in CI; stdout is JSON-RPC only |

**Grok scope:** Grok refuses project-scoped (`./.grok/config.toml`) stdio servers in an
untrusted folder ("folder untrusted"). Use `-s user`, or trust the project in Grok.

### Not verified

- **Other versions of these clients:** older or newer versions, and the desktop or IDE variants of Claude, Codex and Grok, were not tested.
- **Cloud-hosted clients** (web chat products, cloud agents): they cannot start a local stdio process. They would need a local bridge or a remote transport, which this server does not provide (see #22). Local file access must never be assumed there.
- **Transports:** no HTTP or SSE transport exists. Only stdio is supported.

## Copyable configurations

Replace `/path/to/kdenlive-mcp` with the absolute path of the `kdenlive-mcp` executable
(`command -v kdenlive-mcp` after `pipx install mcp-kdenlive`), or use the `uv run` form for a
source checkout. No secrets are required.

**Claude Code**, `mcp.json` (or `claude mcp add kdenlive -- /path/to/kdenlive-mcp`):

```json
{ "mcpServers": { "kdenlive": { "command": "/path/to/kdenlive-mcp", "args": [] } } }
```

**Codex CLI**, `~/.codex/config.toml`:

```toml
[mcp_servers.kdenlive]
command = "/path/to/kdenlive-mcp"
args = []
```

**OpenCode**, `opencode.json` (project) or the global config:

```json
{ "$schema": "https://opencode.ai/config.json",
  "mcp": { "kdenlive": { "type": "local", "command": ["/path/to/kdenlive-mcp"], "enabled": true } } }
```

**Grok Build CLI:**

```sh
grok mcp add kdenlive /path/to/kdenlive-mcp      # user scope
grok mcp doctor kdenlive                         # expect: handshake OK, 5 tools discovered
```

**With uv, no install**: command `uvx`, args `["mcp-kdenlive"]`. The package also provides an
`mcp-kdenlive` command, so this works; it is also how the MCP Registry entry describes it.

**From a source checkout** (any client): command `uv`, args
`["run", "--directory", "/path/to/repo", "kdenlive-mcp"]`.

## Smoke test without an AI client

```sh
npx @modelcontextprotocol/inspector --cli /path/to/kdenlive-mcp --method tools/list
npx @modelcontextprotocol/inspector --cli /path/to/kdenlive-mcp --method tools/call \
    --tool-name diagnose_environment
```

## Environment and PATH issues

- **Folders the server can reach:** without `KDENLIVE_MCP_ALLOWED_ROOTS` in the client's `env` for the server, only your videos directory is allowed (since 0.2). Add your project and media folders there, separated by `:`.
- **Reduced environment:** clients start the server with their own, often reduced, environment. GUI-launched clients may not inherit your shell's `PATH`. Use absolute paths for the server command. If `diagnose_environment` reports Kdenlive, melt or ffprobe as missing only from the client, set `KDENLIVE_MCP_KDENLIVE`, `KDENLIVE_MCP_MELT` and `KDENLIVE_MCP_FFPROBE` in the client's `env` for the server.
- **Clean stdout:** the server writes only protocol messages to stdout. Use `--log-level DEBUG` to get diagnostics on stderr; most clients show them in their MCP logs.
- **Absolute paths:** paths passed to tools must be absolute. The server's working directory is whatever the client chose and carries no meaning.

## Optional protocol features

- **Tools only:** the server exposes tools only. No resources, prompts, sampling, elicitation or roots are required.
- **Text fallback:** clients without structured-output support read the JSON text content; a test checks it is identical to `structuredContent`.
- **Annotations:** tool annotations (`readOnlyHint`, …) are advisory and safe to ignore.
