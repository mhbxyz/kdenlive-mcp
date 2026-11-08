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
