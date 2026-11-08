# Versioning, compatibility and deprecation policy

## Version scheme

Versions follow [PEP 440](https://peps.python.org/pep-0440/) with semantic-versioning meaning:
`MAJOR.MINOR.PATCH`, plus pre-releases `X.Y.ZaN` / `bN` / `rcN` and development builds `X.Y.Z.devN`.

| Change | Before 1.0 (`0.y.z`) | From 1.0 |
|---|---|---|
| Breaking change to a public interface | new **minor** (`0.2.0`) | new **major** |
| New tool, new optional parameter, new result field, new error code | new **minor** | new **minor** |
| Bug fix, docs, internal change with identical public behaviour | new **patch** | new **patch** |

Before 1.0, the project is pre-alpha: interfaces may still change, but only in a new minor
version, with notes in the changelog. Pre-releases (`0.2.0rc1`) go to TestPyPI and may go to
PyPI. `main` carries a `.devN` version that is never published to PyPI.

Examples:

- removing `audio_track` from `insert_clip` → `0.2.0`;
- adding a `lock` option to `add_track` → `0.2.0`;
- fixing a wrong duration in `get_timeline` → `0.1.1`.

## Public interfaces

These are covered by the policy:

1. **Distribution and CLI:**
   - the PyPI name `mcp-kdenlive` and the import package `mcp_kdenlive`;
   - the `kdenlive-mcp` command, its `serve` and `diagnose [--json]` subcommands and their exit statuses;
   - the documented `KDENLIVE_MCP_*` environment variables.
2. **MCP tools:**
   - tool **names**;
   - **input schemas**: parameter names, types, defaults;
   - **output schemas**: field names and meaning;
   - **annotations**: read-only or destructive.
3. **Error contract:** the bracketed **error codes** at the start of tool error messages (listed below), not the wording that follows.
4. **Time and path semantics** documented in `docs/tools.md`: frames, `[start, end)` and inclusive source ranges, path resolution rules.

These are **not** public:

- Python modules and functions: they are internal, even if importable;
- human-readable messages and hints;
- the exact XML layout written into projects, beyond being valid and Kdenlive-editable;
- `research/` and `scripts/`.

`tests/test_interface.py` snapshots the tool names, their parameters and the error codes, so a
change to the public surface fails CI until the snapshot and the changelog are updated together.

### Error codes (v0.1)

`ambiguous_sequence`, `clip_not_found`, `conflict`, `dependency_missing`, `incompatible_track`,
`internal_error`, `invalid_argument`, `invalid_configuration`, `invalid_output`, `invalid_path`,
`invalid_range`, `invalid_xml`, `media_unreadable`, `missing_media`, `not_a_file`,
`not_a_project`, `not_found`, `open_in_kdenlive`, `output_exists`, `output_in_use`, `overlap`,
`path_not_allowed`, `permission_denied`, `profile_required`, `queue_full`, `remote_media`,
`render_failed`, `revision_required`, `sequence_not_found`, `source_protected`,
`stale_revision`, `too_large`, `too_many_tracks`, `track_not_found`, `unknown_job`,
`unknown_preset`, `unknown_profile`, `unsafe_xml`, `unsupported_kdenlive`,
`unsupported_structure`.

The MCP **protocol** version is negotiated by the MCP SDK with each client and is unrelated to
the package version.

## One version, checked everywhere

`pyproject.toml` `project.version` is the single source. Everything else derives from it or is
checked against it by `scripts/check_version.py` (run in CI and before any release):

- **Python and CLI:** `mcp_kdenlive.__version__`, `kdenlive-mcp --version` and the MCP `serverInfo.version` come from the installed metadata.
- **Release tag:** `vX.Y.Z`, which must equal the package version.
- **MCP Registry:** `server.json` `version` and its `packages[].version` (#33).
- **Changelog:** `CHANGELOG.md` must have a `## [X.Y.Z]` section for a release.

## Deprecation

- **How a deprecation is announced:**
  - in the changelog (*Deprecated* section);
  - in the tool or parameter description, which starts with `Deprecated:` and names the replacement;
  - when used, in a `warnings` entry of the result where the result has one.
- **How long it stays:** a deprecated tool, parameter or field keeps working for **at least one minor release** before removal (`0.y` → `0.y+1`; from 1.0, until the next major).
- **Error codes** are never reused with a different meaning.

## External software support

Each Kdenlive, MLT, FFmpeg, Python and MCP-client combination has one of these statuses
(`docs/compatibility.md`, `docs/clients.md`):

| Status | Meaning |
|---|---|
| **tested** | Exercised by CI or by recorded local runs for this release. |
| **supported** | Expected to work, with fixes accepted, but not exercised for this release. |
| **untested** | No evidence either way. |
| **unsupported** | Known not to work; the server refuses with a clear error where it can (for example `kdenlive --render` on Kdenlive 23.08 → `[unsupported_kdenlive]`). |

If a new Kdenlive version changes the project format or a behaviour the server relies on, a
release documents it in the changelog and in `docs/compatibility.md`. The server refuses the
affected operations (`[unsupported_structure]`, `[unsupported_kdenlive]`) rather than writing
files that the new version would misread. Support for a new document version is a minor
release.
