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

## Path to 1.0

1.0 is a stability promise: from 1.0 on, a breaking change to a public interface needs a new
major version. It is released when **all** of these hold (#64, owner decisions of 2026-11-08):

1. **Scope:** every issue of milestones M6 (v0.2), M7 (v0.3) and M8 (v1.0) is closed. This
   includes titles (#15) and subtitles (#16). Issues in *M9 – After 1.0* are additive and ship as
   minor versions after 1.0.
2. **Interface:** the tool interface review (#63) is done for every tool, including the tools
   added in M7. Breaking changes it finds are made before 1.0.
3. **Compatibility:** Kdenlive from 23.08 to the latest release is **tested** for reading,
   editing and rendering (through melt where `kdenlive --render` is missing, #42), both as
   distribution packages and as a Flatpak (#65), with fixtures from 24.x and 25.x (#66).
4. **Manual verifications:** every pending Kdenlive GUI checklist is done (#67).
5. **No open bug** in M6, M7 or M8.
6. **Release candidate and feedback period** (#68):
   - `1.0.0rc1` is published with the normal release workflow and announced as a pre-release;
   - it is used for **at least 14 days**, on **at least 5 real projects** (copies), edited and
     rendered through at least one tested MCP client; projects and findings are logged in #68;
   - a breaking change or a serious bug found during the period means a fix, `1.0.0rc(N+1)`, and a
     new period of **at least 7 days**;
   - if a new Kdenlive feature release comes out during the period (KDE Gear ships them in April,
     August and December), it is tested before 1.0;
   - 14 days is long enough for several real editing sessions and usually one Kdenlive patch
     release, and short enough to keep momentum.

Defaults that are breaking to change later are settled before 1.0. The first one is the
filesystem restriction: without configuration, tools only reach the user's videos directory
(#60, 0.2.0).

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
`invalid_range`, `invalid_xml`, `marker_not_found`, `media_mismatch`, `media_unreadable`, `missing_media`, `not_a_file`,
`not_a_project`, `not_found`, `open_in_kdenlive`, `output_exists`, `output_in_use`, `overlap`,
`path_not_allowed`, `permission_denied`, `profile_required`, `queue_full`, `remote_media`,
`render_failed`, `revision_required`, `sequence_not_found`, `source_protected`,
`stale_revision`, `subtitles_exist`, `too_large`, `too_many_tracks`, `track_not_found`, `unknown_job`,
`unknown_preset`, `unknown_profile`, `unsafe_xml`, `unsupported_kdenlive`,
`unsupported_structure`.

The MCP **protocol** version is negotiated by the MCP SDK with each client and is unrelated to
the package version.

## Interface review before 1.0 (#63, 0.2)

Every tool was reviewed for names, units, defaults, truncation, annotations and outputs, with a
recorded session of a real client (Claude Code, Opus 5.5) doing a six-step in-place edit.

| Topic | Decision |
|---|---|
| Names | Tools are `verb_object`. Every tool that writes a project takes `destination` (`create_project`'s `output` was renamed, **breaking**); renders keep `output` (a media file). |
| Clip references | `<track>@<start>` (`V1@90`) instead of `index:layer:entry`, which shifted after every edit because blanks were counted (**breaking for stored refs**; the old form is still accepted). |
| Groups | `get_timeline` clips list their `group`. |
| Concurrency guards | `revision` (refs) and `expected_sha256` (in-place writes) stay separate concepts; tools with `revision` use it as `expected_sha256` when omitted. |
| No-op writes | Nothing is written, and no backup made, when the content would not change (`saved.written`). Backups are rotated (5 per file). |
| Units | Frames for every timeline position and duration, with timecodes alongside in reads; seconds only for timeouts and media durations; dB for levels. |
| Truncation | `get_timeline.max_clips` and `list_project_media.max_items` report `truncated` and totals. Edits never rely on truncated views. |
| Outputs | Every tool returns an object with a schema (`list_profiles` now returns `{profiles}`, **breaking**), covered by `tests/interface_snapshot.json` (inputs and top-level outputs). |
| Annotations | Reads: read-only, idempotent. Writes: destructive (they may replace a file with `overwrite`), not idempotent. `cancel_render_job`: not read-only, idempotent. |
| Error codes | One catalog (below); every code is raised in `src/` and expected by at least one test (`test_every_error_code_is_exercised_by_a_test`). `not_found` is only for project files; markers use `marker_not_found`. |

Tools added in M7 get the same review before `1.0.0rc1` (#68).

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
