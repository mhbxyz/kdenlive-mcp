# Official MCP Registry

The server is listed in the official MCP Registry (<https://registry.modelcontextprotocol.io>)
as **`io.github.mhbxyz/kdenlive-mcp`**. The registry stores only metadata: the package itself is
the PyPI distribution `mcp-kdenlive`.

> The registry is **in preview** (checked 2026-11-08): breaking changes or data resets may happen.
> If an entry disappears after a reset, publish the current version again from its tag.

## How an entry is published

- **When:** automatically, in `release.yml`, for **final** tags only, **after** the same version is on PyPI.
- **Publisher tool:** `mcp-publisher` is pinned (`MCP_PUBLISHER_VERSION`, v1.8.4). Its archive is verified with Sigstore (`cosign verify-blob`) against the registry's release workflow identity before use.
- **Steps:** `mcp-publisher validate`, then `login github-oidc` (namespace `io.github.mhbxyz/*` proven by GitHub OIDC; no token), then `publish`.
- **Check:** the job queries the public API until the entry lists the new version.

### Ownership proof

The PyPI long description (`README.md`) contains `<!-- mcp-name: io.github.mhbxyz/kdenlive-mcp -->`.
`tests/test_packaging.py` and `scripts/check_dist.py` fail if it disappears.

## `server.json`

- **Schema:** `2025-12-11`. Validate locally with `mcp-publisher validate server.json`.
- **Package:** `pypi` · `mcp-kdenlive` · transport `stdio` · `runtimeHint: uvx`. The package installs both `kdenlive-mcp` and `mcp-kdenlive` commands, so `uvx mcp-kdenlive` works.
- **Environment variables:** the optional `KDENLIVE_MCP_*` variables are listed. None is secret.
- **Version:** `version` and `packages[].version` must equal the package version. `scripts/check_version.py` enforces it in CI and at release.

## Other Kdenlive servers

Other, unrelated Kdenlive MCP servers exist, for example `io.github.Va1bhav512/kdenlive-mcp-server`
in the registry and the PyPI package `kdenlive-mcp` by IO-AtelierTech. This project is
`io.github.mhbxyz/kdenlive-mcp` / `mcp-kdenlive`.

## Correcting or withdrawing a version

Published versions cannot be edited. Publish a new version, then change the old one's status.
`mcp-publisher login github` works interactively for the owner.

```sh
mcp-publisher status --status deprecated --message "Use 0.1.1" io.github.mhbxyz/kdenlive-mcp 0.1.0
mcp-publisher status --status deleted --message "Security issue, see advisory" io.github.mhbxyz/kdenlive-mcp 0.1.0
```

`deleted` is for security or legal problems only (see `docs/incident-response.md`).
