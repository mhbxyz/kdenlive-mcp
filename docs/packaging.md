# Packaging

## Distribution

| Item | Value |
|---|---|
| Distribution name | `mcp-kdenlive` |
| Import package | `mcp_kdenlive` |
| Console script | `kdenlive-mcp` (starts the stdio MCP server; `kdenlive-mcp diagnose` checks the environment) |
| MCP Registry name | `io.github.mhbxyz/kdenlive-mcp` (marker in README, see #33) |
| License | MIT (`License-Expression: MIT`, `LICENSE` shipped in both artifacts) |
| Build backend | `hatchling` |
| Supported Python | 3.11, 3.12, 3.13, 3.14 (`requires-python = ">=3.11"`) |
| Platform | Linux. The code is pure Python, but only Linux is tested and supported. |

### Why not `kdenlive-mcp` on PyPI?

On 2026-11-08 the PyPI name `kdenlive-mcp` already belonged to an unrelated project
(IO-AtelierTech, `IO-AtelierTech/kdenlive-automation`, versions 0.1.0–0.2.1). The owner chose
`mcp-kdenlive` as the fallback (see #24). It was free on both PyPI and TestPyPI on that date.
A name is only secured by the first upload, and configuring a pending Trusted Publisher does
not reserve it.

**Warning for users:** `pip install kdenlive-mcp` / `pipx install kdenlive-mcp` installs the
*other* project. Install `mcp-kdenlive`.

## Dependencies

- **Runtime:** `mcp>=2.3,<3` (official MCP SDK; v2 is a breaking major version) and `pydantic>=2.11,<3`. Ranges are bounded by major version only; no exact pins at runtime.
- **Development:** the `dev` dependency group (pytest, anyio). `uv.lock` pins the development environment for reproducibility; it does not constrain users.
- **System programs:** Kdenlive, MLT `melt` and FFmpeg `ffprobe` are **not** installed by Python packaging. Neither are MLT plugins, codecs or fonts. Install them from your distribution; `kdenlive-mcp diagnose` reports what is missing.

## Installing

Once a release is published (not yet available):

```sh
pipx install mcp-kdenlive           # recommended: isolated environment, kdenlive-mcp on PATH
pipx upgrade mcp-kdenlive
pipx uninstall mcp-kdenlive
```

Or in a virtual environment:

```sh
python3 -m venv ~/.venvs/kdenlive-mcp
~/.venvs/kdenlive-mcp/bin/pip install mcp-kdenlive
~/.venvs/kdenlive-mcp/bin/kdenlive-mcp diagnose
```

From a source checkout or a locally built artifact:

```sh
uv build                                   # dist/mcp_kdenlive-<version>-py3-none-any.whl and .tar.gz
python scripts/check_dist.py dist          # content and metadata checks
pipx install dist/mcp_kdenlive-*.whl
```

## Verifying an artifact

`scripts/check_dist.py` fails on:

- **Metadata:** a wrong name, Python requirement or license expression, a missing MCP Registry marker, or a version mismatch between the wheel and the sdist.
- **Missing files:** package files, `py.typed`, `LICENSE` or the console script.
- **Accidental content:** the research lab, VCS or CI files, caches, `.kdenlive` projects or key files.

`scripts/smoke_installed.py`, run with the interpreter of the target environment and from
outside the checkout, checks `kdenlive-mcp --version` and a stdio MCP round trip
(`initialize` → `diagnose_environment`), with stdout parsed strictly as JSON-RPC.

Verified on 2026-11-08:

- **Wheel:** installed into clean environments on Python 3.11.14, 3.12.12, 3.13.11 and 3.14.2.
- **sdist:** installed on Python 3.12.
- **pipx:** install, upgrade from `0.1.0.dev0` to `0.1.0.dev1` and uninstall, run with an isolated `PIPX_HOME`.

## Known limitations

- **Linux only:** other platforms may install but are untested.
- **Pre-release:** the package is pre-alpha (`0.1.0.devN`). The version policy is defined in #25.
