# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[docs/versioning.md](docs/versioning.md). Breaking changes are marked **Breaking** and come with
migration notes in the same entry.

## [Unreleased]

### Documentation

- **Path to 1.0:** release criteria, release candidate procedure and feedback period (`docs/versioning.md`, `docs/releasing.md`, #64).

### Development

- **Tests:** an end-to-end level (`tests/e2e`, real server over stdio, renders checked objectively) and line and branch coverage with a floor in CI (#70).

## [0.1.0] - 2026-11-08

First public release.

### Added

- **Server:** stdio MCP server `kdenlive-mcp` (distribution `mcp-kdenlive`), with `diagnose_environment` and a `kdenlive-mcp diagnose` CLI.
- **Inspection:** read-only `inspect_project`, `get_timeline`, `list_project_media`, `probe_media`. They use frame-accurate time with rational frame rates, Kdenlive's path resolution rules, and missing-media and missing-asset diagnostics.
- **Rendering:**
  - `list_render_presets` and `render_project` (`kdenlive --render`), with pre-flight checks, isolation and ffprobe validation;
  - background jobs `start_render`, `get_render_job`, `list_render_jobs`, `cancel_render_job`.
- **Creation and editing:**
  - `list_profiles`, `create_project`;
  - safe saving with `save_project_copy` (copies by default, conflict detection, backups, path rebasing);
  - `add_media_to_bin`, `add_track`;
  - timeline edits `insert_clip`, `move_clip`, `trim_clip`, `split_clip`, `remove_clip`.
- **Security:** `KDENLIVE_MCP_ALLOWED_ROOTS`, an allow-listed child environment, and refusal of remote media and symlinked write targets.

- **Since 0.1.0rc1:** `mcp-kdenlive` command alias (so `uvx mcp-kdenlive` works), publication to the official MCP Registry, user guide.

### Known limitations

- **Interactive editing in Kdenlive:** checked manually only (checklists in #8, #10, #11). Automated checks cover *open → save → compare* in Kdenlive 26.08.2.
- **Rendering on Kdenlive 23.08:** `kdenlive --render` is unavailable there (#42).
- **Rendering with subtitles:** Kdenlive 26.08.2 crashes when rendering projects with subtitles (#16).

## [0.1.0rc1] - 2026-11-08

Release candidate for 0.1.0, published to TestPyPI only to rehearse the release pipeline.
