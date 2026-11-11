# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[docs/versioning.md](docs/versioning.md). Breaking changes are marked **Breaking** and come with
migration notes in the same entry.

## [Unreleased]

### Fixed

- **Titles edited in Kdenlive moved to the left (#15):** a centred or right-aligned title made
  by `add_title` jumped to the left of the frame as soon as it was changed in Kdenlive's title
  editor (*Update Title*). The editor places a text item at its position with the text's own
  width and ignores the wider box the server wrote. The server now measures the text from the
  font file and writes it where it appears, like Kdenlive; `title.box` reports that box. Found
  during the manual checks (#67).
- **melt engine and proxy clips (#42):** a project where Kdenlive used a proxy for a clip was
  rendered from the proxy (Kdenlive stores the proxy as the clip's `resource`), so the result was
  blurred, 640 pixels wide scaled up. The melt engine now renders those clips from their original
  media, like Kdenlive, and warns how many it switched. The `kdenlive` engine was not affected.

- **Rendering projects saved by Kdenlive 24.12 or 25.04 (#86):** when a subtitle ended after the
  last clip, `render_project` and `start_render` rejected a correct render ("duration differs from
  the timeline"). These releases end the project's output at the last subtitle, and melt and
  `kdenlive --render` render that length. The expected duration is now the project's output (its
  project tractor), and the sequence's duration only when the file has none.

### Added

- **Fixtures saved by Kdenlive 24.12.3 and 25.04.3 (#86):** crossfades, subtitles, a montage,
  keyframed effects, guides and a title, each saved by the official AppImages. They read like
  the 26.08.2 files they came from, survive a no-op save, can be edited and render with melt.
  `docs/compatibility.md` lists the format differences. `research/lab/kdx11.py` drives an
  isolated Kdenlive (AppImages included) on a private X display.

### Changed

- **README:** rewritten as the project's front page: what the server does and why, a demo video
  (Claude Code cutting a trailer, Kdenlive showing each saved version, in `assets/`), a
  "Linux only" badge and notice (#98), and the install steps. Settings, client configuration and
  development instructions now live only in the documentation the README links to.

## [0.3.0] - 2026-11-08

Coverage and compatibility (milestone M7): effects and crossfades, titles, subtitles, the melt
render engine, Flatpak Kdenlive, other Kdenlive releases, and four end-to-end demos.
**Contains a breaking change**: unknown tool arguments are now refused (see *Changed*, with its
migration).

### Added

- **Demos (#92):** `examples/demos/` holds four end-to-end scenarios (travel vlog, interview, rescue of a Kdenlive-made project, vertical short), each with a README, the prompt for an assistant, a media generator, a deterministic MCP client script with render checks (also run as e2e tests) and, in `RESULTS.md`, runs by a real client.

- **Subtitles (#16):** `list_subtitles` reads a sequence's subtitles (file, styles, events with frames, layers and text). `import_subtitles` imports a SubRip or WebVTT file (UTF-8, or UTF-16 with a BOM) as the main sequence's subtitles in Kdenlive's own format: an ASS file named after the saved project, written with it (`saved.side_files`). Overlapping cues go to separate layers; existing subtitles are only replaced with `replace=true`, and their file is never modified. New error code `subtitles_exist`. `EditResult` gains `subtitles`; `SaveResult` gains `side_files`.

- **Titles (#15):** `add_title` creates an editable Kdenlive title clip (text with Unicode and several lines, font checked with fontconfig, size, colour, outline, italic, preset or pixel position, duration) and places it on a video track. `EditResult` gains `title`. `insert_clip` accepts title clips from the bin, which the timeline plays from the bin producer like Kdenlive. Titles use Qt 6's font weight (`400` = normal), drawn alike by Kdenlive 26.08.2, its 25.04.3 AppImage, melt 7.42 and melt 7.22 (#92).

- **Effects and crossfades (#14):** `list_effects` lists a verified set of video effects (crop, brightness, greyscale, sepia, gamma, fades from/to black) with parameters, units, ranges, availability in the installed MLT and the tested versions; fades from/to black use the brightness form so they render with Kdenlive 24.12–26.08 and MLT 7.22. `add_effect`, `update_effect` and `remove_effect` edit them on a clip, keeping other effects, their order and keyframes (animated values are not overwritten). `add_mix` and `remove_mix` add or remove a crossfade between two touching clips, in Kdenlive's own format, with the matching audio mix for grouped clips. `EditResult` gains `effects`. Crossfades can follow each other (A→B→C) as in Kdenlive, clips alternating between the track's two layers; clips after a crossfade never move, and a crossfade that would run into a neighbouring clip or crossfade is refused (`[overlap]`) (#92).

- **Flatpak Kdenlive (#65):** when Kdenlive is not on `PATH`, a Flathub installation (`org.kde.kdenlive`, user or system) is detected and used, with its own melt and FFmpeg, MLT profiles and render presets. Paths the sandbox cannot see (`/tmp`, system folders) are refused with `[invalid_path]`. `KDENLIVE_MCP_FLATPAK=off` disables it. Tested with Flathub's 26.08.3.

- **melt render engine (#42):** `render_project` and `start_render` take `engine` (`auto`, `kdenlive`, `melt`). `auto` renders with MLT's melt when Kdenlive has no `--render` (before 24.02, e.g. Ubuntu 24.04's 23.08) or when the project has subtitles (Kdenlive 26.08.2 crashes on them), using the preset's arguments filled with its defaults. Results report the `engine`; melt renders carry a warning that they are not Kdenlive's own render path. With MLT 7.22 (Ubuntu 24.04), whose Qt module needs an X display even offscreen, the melt engine passes `DISPLAY`, `WAYLAND_DISPLAY` and `XAUTHORITY` and fails with an explanation (display, `xvfb-run`, newer MLT) when the module cannot load, instead of rendering titles as *INVALID* and dropping track compositing (#90).

### Fixed

- **Release: TestPyPI visibility (#82):** the release waits up to 15 minutes, with backoff, for TestPyPI to list the new version, says how long it waited and fails clearly (`scripts/wait_for_index.py`); what is verified is unchanged.

- **`kdenlive --render` taken for an open project (found by a real-client run, #92):** command-line renders running at the same time made edits warn that Kdenlive might have the project open; they are now ignored by the open-project check.

### Changed

- **Breaking — unknown arguments are refused (#15):** every tool now answers `[invalid_argument]` naming the unknown arguments and listing the accepted ones, instead of silently ignoring them (e.g. `bold` on `add_title`). Input schemas declare `additionalProperties: false`. **Migration:** drop arguments a tool does not list.

- **Work folders (#65):** temporary folders for renders and MLT probes are created in `$XDG_CACHE_HOME/kdenlive-mcp/tmp` instead of the system temp folder.

- **Rendering on Kdenlive 23.x (#42):** `render_project` no longer fails with `[unsupported_kdenlive]` there by default; it renders with melt. Pass `engine="kdenlive"` to require Kdenlive's own render path.

### Documentation

- **Other Kdenlive releases (#66):** the official 24.12.3 and 25.04.3 AppImages, through `KDENLIVE_MCP_KDENLIVE`, are detected and render projects written by this server and by Kdenlive 26.08.2 (`docs/compatibility.md`; integration test with `KDENLIVE_MCP_TEST_KDENLIVE`). Projects *saved* by these releases are tracked in #86.

## [0.2.0] - 2026-11-08

Editing essentials (milestone M6). **Contains breaking changes**: read the *Changed* section and its migrations before upgrading.

### Changed

- **Breaking — clip references (#63):** `get_timeline` refs are now `<track>@<start>` (e.g. `V1@90`), stable while a clip does not move. Edit tools still accept the 0.1 form `index:layer:entry`. **Migration:** read refs from `get_timeline` instead of building them.
- **Breaking — `create_project` parameter (#63):** `output` is now `destination`, like every other tool that writes a project. **Migration:** rename the argument.
- **Breaking — `list_profiles` result (#63):** an object `{profiles: [...]}` instead of a bare list. **Migration:** read `profiles`.
- **Backups (#63):** backups are rotated: the first one (the project before the server's first edit) and the 4 most recent are kept. Only backups written since 0.2 are rotated; their names now include microseconds, and 0.1 backups or other files are never removed.
- **Groups (#63):** a clip inside a user group (Ctrl+G), even one containing an audio/video pair, is now refused by edit tools instead of being moved without its group.
- **Trims and splits with effects (#13):** edge fades (audio fades, fades from/to black) now stay at the clip's edges when it is trimmed or split, and shrink to fit a shorter clip; a split gives the fade-in to the left half and the fade-out to the right half, with new filter ids. A constant (single-keyframe) effect value no longer blocks trimming the start or splitting; animated effects still do.
- **Breaking — filesystem scope by default (#60):** without `KDENLIVE_MCP_ALLOWED_ROOTS`, the tools now only read and write inside the user's videos directory (`XDG_VIDEOS_DIR`, usually `~/Videos`) and refuse other paths with `[path_not_allowed]`. Before, the unset variable meant no restriction.
  - **Migration:** if your projects or media are elsewhere, add `"env": {"KDENLIVE_MCP_ALLOWED_ROOTS": "/path/one:/path/two"}` to the server entry of your MCP client. `KDENLIVE_MCP_ALLOWED_ROOTS=unrestricted` restores the 0.1 behaviour (not recommended).
  - `kdenlive-mcp diagnose` prints the effective folders, and the server logs them on stderr at startup.

### Added

- **Interface review (#63):** `get_timeline` clips list their `group`; edit results give each changed clip's new `ref`; `saved.written` tells whether anything was written (an unchanged project is not rewritten and gets no backup); tools with `revision` use it as `expected_sha256` for in-place edits; the interface snapshot covers outputs; every error code is tested; MCP protocol revisions and SDK range documented in `docs/compatibility.md`. New error code `marker_not_found` (markers are new in 0.2).
- **Guides and clip markers (#12):** `list_markers`, `add_marker`, `update_marker` and `remove_marker` for timeline guides (chapters) and clip markers, with comments, categories and durations, in Kdenlive 26.08's format.
- **Relink media (#17):** `relink_media` points bin clips at moved or replacement files, on the bin clip and every timeline instance, after checking with MLT that the new file covers what the timeline uses. New error code `media_mismatch`.
- **Audio level and fades (#13):** `set_clip_volume` (constant dB level, Kdenlive's *Volume* effect) and `set_clip_fades` (fade-in/fade-out in frames, Kdenlive's *Fade in*/*Fade out*) on a clip's audio. `get_timeline` reports each clip's `audio` settings.
- **Editing tracks with crossfades (#61):** `insert_clip`, `move_clip`, `trim_clip`, `split_clip` and `remove_clip` now work on tracks that contain Kdenlive same-track mixes. Mixed clips keep their mix: they cannot be moved, their mixed side cannot be trimmed, and they cannot be split inside the mix. Removing a mixed clip removes its mixes and trims the partner back to the original cut. Verified with mixes made by Kdenlive 26.08.2.
- **`get_timeline` mixes:** a `cut` field, the frame where the clips met before the mix (#61).
- **`remove_clip` result:** `clips` now lists the neighbours trimmed back to a cut when a crossfade was removed with the clip; it is empty otherwise, as before (#61).
- **Open-project detection (#62):** writing over a project shown by a running Kdenlive is refused even when it was opened from Kdenlive's *Open* dialog without changes: the server reads Kdenlive's window title over the session D-Bus (read-only). When a running Kdenlive cannot be checked, the write carries a warning to close or reload the project.
- **`diagnose_environment`:** a `filesystem` field with the effective roots, their source (`default`, `environment`, `unrestricted`) and the ones that do not exist (#60).

### Known limitations

- **Manual Kdenlive checks pending** for crossfade editing (#61), open-project detection (#62), audio level and fades (#13) and guides and markers (#12): automated round trips through Kdenlive 26.08.2 pass, but interactive checks (playback, undo/redo, GUI editing of server-written data) wait for a person. Checklists are linked from #67.

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
