# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[docs/versioning.md](docs/versioning.md). Breaking changes are marked **Breaking** and come with
migration notes in the same entry.

## [Unreleased]

### Changed

- **Trims and splits with effects (#13):** edge fades (audio fades, fades from/to black) now stay at the clip's edges when it is trimmed or split, and shrink to fit a shorter clip; a split gives the fade-in to the left half and the fade-out to the right half, with new filter ids. A constant (single-keyframe) effect value no longer blocks trimming the start or splitting; animated effects still do.

- **Breaking — filesystem scope by default (#60):** without `KDENLIVE_MCP_ALLOWED_ROOTS`, the tools now only read and write inside the user's videos directory (`XDG_VIDEOS_DIR`, usually `~/Videos`) and refuse other paths with `[path_not_allowed]`. Before, the unset variable meant no restriction.
  - **Migration:** if your projects or media are elsewhere, add `"env": {"KDENLIVE_MCP_ALLOWED_ROOTS": "/path/one:/path/two"}` to the server entry of your MCP client. `KDENLIVE_MCP_ALLOWED_ROOTS=unrestricted` restores the 0.1 behaviour (not recommended).
  - `kdenlive-mcp diagnose` prints the effective folders, and the server logs them on stderr at startup.

### Added

- **Audio level and fades (#13):** `set_clip_volume` (constant dB level, Kdenlive's *Volume* effect) and `set_clip_fades` (fade-in/fade-out in frames, Kdenlive's *Fade in*/*Fade out*) on a clip's audio. `get_timeline` reports each clip's `audio` settings.

- **Editing tracks with crossfades (#61):** `insert_clip`, `move_clip`, `trim_clip`, `split_clip` and `remove_clip` now work on tracks that contain Kdenlive same-track mixes. Mixed clips keep their mix: they cannot be moved, their mixed side cannot be trimmed, and they cannot be split inside the mix. Removing a mixed clip removes its mixes and trims the partner back to the original cut. Verified with mixes made by Kdenlive 26.08.2.
- **`get_timeline` mixes:** a `cut` field, the frame where the clips met before the mix (#61).
- **`remove_clip` result:** `clips` now lists the neighbours trimmed back to a cut when a crossfade was removed with the clip; it is empty otherwise, as before (#61).
- **Open-project detection (#62):** writing over a project shown by a running Kdenlive is refused even when it was opened from Kdenlive's *Open* dialog without changes: the server reads Kdenlive's window title over the session D-Bus (read-only). When a running Kdenlive cannot be checked, the write carries a warning to close or reload the project.
- **`diagnose_environment`:** a `filesystem` field with the effective roots, their source (`default`, `environment`, `unrestricted`) and the ones that do not exist (#60).

### Fixed

- **`get_timeline` mixes (#61):** `end` was one frame too late. Kdenlive stores a mix's `out` as the left clip's exclusive end. `cut_offset` is now documented correctly: frames from the cut to the **end** of the mix.

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
