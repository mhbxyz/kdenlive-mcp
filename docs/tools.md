# MCP tools

Every tool runs locally and declares MCP annotations. Errors are returned as tool errors
whose text starts with a stable code in brackets, e.g. `[not_found] … Hint: …`.

| Tool | Read-only | Purpose |
|---|---|---|
| `diagnose_environment` | yes | Kdenlive, melt, ffprobe and ffmpeg availability, versions, override variables |
| `inspect_project` | yes | Project summary: versions, profile, bin size, sequences |
| `get_timeline` | yes | Tracks, clips, mixes, compositions and guides of one sequence |

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
