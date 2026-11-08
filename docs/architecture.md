# Architecture and technology choices

## Runtime and SDK

| Choice | Decision | Why |
|---|---|---|
| Language | Python ≥ 3.11 | Decided in #1/#24; rich XML, subprocess and packaging tooling, `pipx` installs. |
| MCP SDK | Official [`mcp`](https://pypi.org/project/mcp/) Python SDK, `>=2.3,<3` | Maintained by the Model Context Protocol project. v2 provides `MCPServer`, typed structured output and an in-process `Client` for tests. |
| Transport | stdio only | Local-first; no listener. Remote access is future work (#22). |
| Data models | pydantic v2 (already required by `mcp`) | JSON Schema for tool outputs, validation of inputs. |

The SDK negotiates the protocol version with each client. Both 2026-07-28 clients (modern SDK
`Client`) and 2025-era clients using the `initialize` handshake (`2025-06-18`) are covered by
tests. The package version is not the protocol version.

## Layers

```text
cli.py          argument parsing, logging to stderr, starts the server
server.py       MCP transport: tool registration, annotations, schemas  (only module importing mcp)
environment.py  domain: locate and probe external programs
process.py      domain: safe subprocess execution (argument lists, process groups, timeouts)
```

Rules:

- **Dependency direction:** domain modules never import `server.py` or `mcp`.
- **No shell:** subprocesses always use argument lists, closed stdin, a timeout, and a process-group kill on timeout.
- **Clean stdout:** stdout belongs to the protocol. Diagnostics use `logging` on stderr.
- **Read-only first:** tools that only read declare `readOnlyHint`.
