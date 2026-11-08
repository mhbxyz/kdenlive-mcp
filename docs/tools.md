# MCP tools

Every tool runs locally and declares MCP annotations. Errors are returned as tool errors
whose text starts with a stable code in brackets, e.g. `[not_found] … Hint: …`.

| Tool | Read-only | Purpose |
|---|---|---|
| `diagnose_environment` | yes | Kdenlive, melt, ffprobe and ffmpeg availability, versions, override variables |
| `inspect_project` | yes | Project summary: versions, profile, bin size, sequences |
| `get_timeline` | yes | Tracks, clips, mixes, compositions and guides of one sequence |
| `list_project_media` | yes | Bin media with resolved paths and status, effect/transition asset files, optional ffprobe data |
| `probe_media` | yes | ffprobe metadata for one local file |
| `list_render_presets` | yes | Render presets of the installed Kdenlive |
| `render_project` | no (creates a file) | Render with `kdenlive --render`, then validate the output |
| `start_render` | no (creates a file) | Same as `render_project`, in the background; returns a job id |
| `get_render_job` / `list_render_jobs` | yes | Job state, progress when available, final result |
| `cancel_render_job` | no | Cancel a queued or running job |

## `inspect_project(path)`

`path` must be the **absolute** path of a `.kdenlive` file. The result contains:

- **File:** `file` gives the size, `sha256` and modification time. Compare `sha256` between calls to detect changes.
- **Versions:** `kdenlive_version` (Kdenlive that last saved the file), `document_version` and `format_generation`. Generation 5 means Kdenlive 23.04 or later with multiple sequences; generation 4 means 20.08 to 22.12.
- **Profile:** `profile`, with the rational `frame_rate` `{num, den}`, the size, the aspect ratios and the colorspace.
- **Bin and sequences:** `bin_clip_count` (sequence clips excluded) and `sequences`, each with its uuid, name, active flag, duration and track and clip counts.

## `get_timeline(path, sequence=None, max_clips=500)`

`sequence` accepts a sequence uuid or name; without it, the active sequence is returned.

### Time conventions

- **Units:** every position is an **integer frame** at the project rate. `*_timecode` fields are display labels (non-drop `HH:MM:SS:FF`).
- **Timeline range:** `[start, end)`, with `end = start + duration`.
- **Source range:** `source_in` and `source_out` are **inclusive** (MLT semantics), so `duration = source_out − source_in + 1`.
- **Conversion:** Kdenlive stores times as `HH:MM:SS.mmm`, SMPTE or frame counts. They are converted with the exact rational rate and rounded to the nearest frame. This is lossless for all values Kdenlive writes at 23.976, 25, 29.97, 50 and 59.94 fps (tested).

### Tracks

- **Ordering:** tracks are listed in MLT order. `index` is the MLT track index (0 is the background and is omitted).
- **Labels:** `label` follows Kdenlive's convention, derived from the order. `V1` is the lowest video track and `A1` is the audio track directly below the video tracks.
- **Layers:** each Kdenlive track has two playlists. `layer` 1 holds the second clip of a same-track mix, and `mixes` lists those transitions.
- **Clip references:** `ref` (`track:layer:entry`) identifies a clip *in this file revision only*.

### Limits

- **Disk only:** the file on disk is read, not the state of a running Kdenlive. Unsaved changes are invisible, and Kdenlive may overwrite the file on its next save.
- **No editing:** the tool never writes, and refuses DTDs or entities, files over 64 MiB, non-Kdenlive XML and relative paths.
- **Truncation:** large timelines are cut after `max_clips` clips; `truncated`, `returned_clips` and `total_clips` say so.
- **Tested versions:** files written by Kdenlive 22.07 to 26.08.2 (document versions 1.04 and 1.1). Older documents are read best effort, with a warning.
- **Stored durations:** Kdenlive 26.08.2 sometimes stores a sequence duration (`kdenlive:duration` and the project tractor `out`) one frame longer than the end of the last clip. This server computes durations from the clips.

## `list_project_media(path, probe=False, max_items=200)`

### Status values

| `status` | Meaning |
|---|---|
| `ok` | A readable regular file exists at the resolved path. |
| `missing` | Nothing exists there, or the path is a broken symlink. |
| `inaccessible` | The path exists but cannot be read: permissions, a directory, or a name that is too long. |
| `remote` | A URL. It is reported and **never fetched**. |
| `generated` | No file is needed: color, title, generator, sequence. |
| `pattern` | An image-sequence pattern. Only its directory is checked. |
| `unknown` | No resource, or an unrecognised producer. |

### What each item reports

- **Paths:** `reference` is the value stored in the project; `resolved_path` is the absolute path actually checked.
- **Proxies:** when a proxy is in use, the item's status is the status of the *original* media, and `proxy_status` is reported separately (Kdenlive rebuilds missing proxies).
- **Effect and transition files:** `asset_files` lists files used by effects and transitions, the same ones Kdenlive checks when loading. Those are LUTs (`avfilter.lut3d` `av.file`), shapes, luma and wipe images, and subtitles.
- **Missing list:** `missing` gathers every missing clip or asset path in one place.
- **Probing:** with `probe=true`, up to 50 available files are probed with ffprobe.

### Path resolution (same rules as Kdenlive 26.08.2)

1. Relative references resolve against the `<mlt root="…">` directory. Without `root`, the project file's directory is used. The server's working directory is never used.
2. If `root` no longer exists, the project was **moved**. The project file's directory is used instead, and absolute references starting with the old root are rewritten (`rewritten_from_moved_root`).
3. `$CURRENTPATH` (archived projects) means the project file's directory. `file://` URLs are treated as local paths.

### Filesystem scope

The server runs locally with the permissions of the user who started it. It reads only the
paths it is given and the media those projects reference, and never writes them. A
configurable allow-list of roots is tracked in #27. ffprobe is only called on local
regular files, with `-protocol_whitelist file` and a `file:` prefix, so names containing `:`
are not interpreted as protocols.

### Not checked

Fonts and images inside titles, installed effect plugins, and full media decodability.

## `probe_media(path)`

Absolute local path only; URLs and relative paths are refused. Returns the file status and
ffprobe data: container, duration, size, and per stream the codec, dimensions, rational
`frame_rate`, `rotation`, sample rate and channels. Missing metadata is `null`.

## `list_render_presets()`

Reads Kdenlive's preset files in Kdenlive's own order, with later files overriding earlier ones:

1. the first `kdenlive/export/profiles.xml` found in `XDG_DATA_HOME`, `XDG_DATA_DIRS` or next to the `kdenlive` executable;
2. other `*.xml` files in `$XDG_DATA_HOME/kdenlive/export/`;
3. `customprofiles.xml`.

Codec support by the installed MLT and FFmpeg is only known when rendering.

## `render_project(path, output, preset="MP4-H264/AAC", overwrite=False, allow_missing_media=False, timeout_seconds=3600)`

Runs `kdenlive --render --render-preset <preset> <project> <temporary output>`. Kdenlive's
own behaviour drives the checks:

| Kdenlive behaviour observed (26.08.2) | What the server does first |
|---|---|
| Unknown preset name: exits 0 and silently renders with another preset | Rejects names absent from the preset files (`unknown_preset`) |
| Missing media: exits 0 and renders gaps | Rejects (`missing_media`) unless `allow_missing_media=true`, which adds a warning |
| Nonexistent project: crashes (exit 139) | The project is loaded and validated first |
| Existing output: refuses (exit 1) | `output_exists`, unless `overwrite=true` |
| `--render` / `--render-preset` unknown in Kdenlive 23.08 (Ubuntu 24.04) | `unsupported_kdenlive`, detected from `kdenlive --help`; a `melt` fallback is tracked in #42 |

### Execution

- **Atomic publication:** the render goes to a hidden temporary file next to the output. Only after a successful, validated render is it moved into place (atomic `rename`). With `overwrite=true`, an existing file is replaced only at that moment, so a failed render leaves it untouched.
- **Isolation:** Kdenlive runs headless (`QT_QPA_PLATFORM=offscreen`), without a session D-Bus, and with a private, temporary configuration and cache directory. Your Kdenlive settings, recent files and thumbnail cache are not touched. Custom presets in `XDG_DATA_HOME` remain available.
- **Timeout:** the process group (Kdenlive and `kdenlive_render`) is stopped when `timeout_seconds` is exceeded.
- **Separate outcomes:** `process_completed` says whether Kdenlive exited successfully. `validation` is a separate ffprobe check: at least one audio or video stream, and a duration within max(0.25 s, 2 %) of the active sequence. `state` is `succeeded` only if both pass.
- **Disk state only:** the project file on disk is rendered. A project open in Kdenlive with unsaved changes renders as last saved.
- **Output paths:** the output must be an absolute path in an existing, writable directory. It cannot be the project file or one of its media files. A file extension that differs from the preset's is reported as a warning.

### Limits

- **No visual check:** subjective visual or audio correctness is not checked.
- **Defaults:** rendering uses the preset's default quality and speed. Proxies, two-pass encoding, per-track audio and embedded subtitles are disabled, as in Kdenlive's command-line mode.
- **Packaging:** Flatpak and Snap installs need `KDENLIVE_MCP_KDENLIVE` pointing to a wrapper; untested.
- **Blocking:** the call blocks for the whole render. For long renders, use the job tools below.
- **Isolation from a running Kdenlive:** each render also gets a private `TMPDIR`. Otherwise `kdenlive_render` looks for running Kdenlive instances there and reports progress to them.

## Render jobs: `start_render`, `get_render_job`, `list_render_jobs`, `cancel_render_job`

`start_render` takes the same arguments and runs the same checks as `render_project`, but
returns a job immediately.

| State | Meaning |
|---|---|
| `queued` | Waiting for a free slot; `queue_position` 1 starts next. |
| `running` | Kdenlive is rendering; `elapsed_seconds` counts from the start. |
| `succeeded` | The process completed **and** the output passed validation; the file is in place. |
| `failed` | Non-zero exit, no output, failed validation or timeout. See `result.error` and `result.log_tail`. |
| `cancelled` | Stopped on request (or at server exit); partial output removed. |

### Job behaviour

- **Concurrency:** `KDENLIVE_MCP_MAX_RENDERS` renders run at once (default 1, maximum 4). Up to 20 more wait in a queue. Two active jobs cannot target the same output file.
- **Two-step cancellation:** `cancel_render_job` returns at once with `cancel_requested: true`. The state becomes `cancelled` only after the whole process group (Kdenlive, `kdenlive_render`, melt) has exited, which takes SIGTERM then SIGKILL after 5 s, and the partial output has been deleted. Cancelling a finished job changes nothing. A job may finish before a late cancel lands; the final state says what actually happened.
- **Progress:** Kdenlive's command-line render does not report progress reliably. `progress` is filled only when `kdenlive_render` has written a progress line. Otherwise it is `null`, and `progress_note` explains why.
- **Lifetime:** jobs are in memory and belong to one server process. When the server exits (the client closes the stdio session), running renders are stopped and cleaned up. Nothing persists across restarts: an unknown job id returns `[unknown_job]`. The 50 most recent finished jobs are kept.
- **Abrupt exit:** if the server is killed with SIGKILL, it cannot stop its renders. Kdenlive keeps running until it finishes, and its partial `.<name>.kdenlive-mcp-*.partial.*` file stays next to the output.
