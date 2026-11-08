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
| `list_profiles` | yes | MLT video profiles usable for new projects |
| `create_project` | no (creates a file) | New Kdenlive project with one media clip on the timeline |
| `save_project_copy` | no (writes a file) | Verified copy of a project; the base of every editing tool |
| `add_media_to_bin` | no (writes a file) | Add audio/video files to the bin |
| `add_track` | no (writes a file) | Add a named audio or video track |
| `insert_clip` / `move_clip` / `trim_clip` / `split_clip` / `remove_clip` | no (writes a file) | Frame-accurate timeline edits |
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
- **Layers:** each Kdenlive track has two playlists. `layer` 1 holds the second clip of a same-track mix, and `mixes` lists those transitions: `start` is where the right clip starts, `end` (exclusive) where the left clip ends, and `cut` the frame where the clips met before the mix (`end - cut_offset`).
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
paths it is given and the media those projects reference, and never writes them. Every path
must be inside the allowed roots (`KDENLIVE_MCP_ALLOWED_ROOTS`; unset means the user's videos
directory, see [security.md](security.md)). ffprobe is only called on local
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

## `create_project(output, media_path, profile="auto", overwrite=False)`

Creates a new `.kdenlive` file. The media clip is in the bin and at frame 0 of
"Sequence 1":

- **Audio and video media:** video on V1, audio on A1, grouped like Kdenlive's own insertion (`AVSplit`).
- **Audio-only media:** A1.
- **Video-only media:** V1.

### How the file is built

- **Skeleton:** an empty project saved by Kdenlive 26.08.2 (`templates/kdenlive-26.08-empty.kdenlive`). It provides the four default tracks, the internal mixing and compositing, the bin folders and the document properties. It gets new document and sequence uuids and a new document id.
- **Profile:** `profile` is an MLT profile name (`list_profiles`). With `auto`, it is the standard progressive profile whose resolution and rational frame rate match the media; if none matches, the call returns `[profile_required]`.
- **Clip data:** length at the project frame rate, stream indexes and `meta.media.*` are computed by MLT (`melt -profile <p> avformat:<file> -consumer xml`), the same data Kdenlive gets from MLT.
- **Kdenlive metadata:** bin id, control uuid, `kdenlive:file_size` and `kdenlive:file_hash`. The hash uses Kdenlive's algorithm: MD5 of the file, or of its first and last MB above 2 MB. It is checked against a hash stored by Kdenlive 26.08.2.
- **Media path:** relative to the project directory when the media is inside it; otherwise absolute, with a warning that moving the media breaks the project.

### Safety

- **Overwrite:** an existing file is refused unless `overwrite=true`.
- **No partial files:** the file is written to a temporary name, synced, then linked into place. Without `overwrite`, the link never replaces a file that appeared meanwhile. A failed creation leaves nothing behind.
- **Media untouched:** media files are never modified.
- **Read-back check:** the written file is read back with the inspection parser and must describe the clip that was built.

### Verification against Kdenlive

`research/lab/verify_roundtrip.py` opens a project in a headless Kdenlive 26.08.2, saves a copy
through *Save As* and compares the timelines. On 2026-11-08 it passed for three generated projects:

- 29.97 fps with audio and video;
- 25 fps with audio and video and a Unicode file name;
- 25 fps audio only.

For each: no dialog while loading, and an identical timeline after Kdenlive's save. Melt and
`kdenlive --render` render generated projects (integration tests).

### Limits

- **Media types:** audio and video files only. Images, titles, colors and image sequences come later (#10, #15).
- **Profiles:** custom Kdenlive profiles from `~/.local/share/kdenlive/profiles` are not offered, only MLT's standard profiles.
- **Metadata refresh:** Kdenlive adds a few `meta.media.*` properties (decoded size, frame rate) when it opens the file. They are recomputed by Kdenlive and not required.
- **Manual checks:** interactive GUI editing of a generated project still needs manual checks (see #8).

## Safe saving: `save_project_copy(path, destination, overwrite=False, expected_sha256=None)`

Every editing tool writes through the same safe path. `save_project_copy` exposes it directly:
it writes a verified copy without changing anything.

### Policy

| Situation | Behaviour |
|---|---|
| New destination | Written atomically: temporary file, fsync, link. |
| Destination exists | `[output_exists]`, unless `overwrite=true`. The old file is first copied to `<name>.backup-<UTC timestamp>.kdenlive`. |
| Destination is the source | `[source_protected]`, unless `overwrite=true` **and** `expected_sha256` equals the sha256 of the version read (from `inspect_project`). A backup is kept. |
| Source changed since it was read | `[conflict]`, nothing written. The source is re-hashed just before writing. |
| Destination appears open in Kdenlive | `[open_in_kdenlive]`, with the evidence (#62): a `kdenlive` process started with that file; a Kdenlive autosave lock of unsaved changes; or a running Kdenlive whose **window title** names a project with the same file name (read-only, over the session D-Bus, see [security.md](security.md), *Open projects*). This last signal also catches a project opened from Kdenlive's *Open* dialog without changes. |
| Kdenlive runs but could not be checked | Written, with a warning in `saved.warnings` telling the user to close or reload the project in Kdenlive. This happens without `busctl` (systemd) or a session bus, or with a Kdenlive that is not on the session bus. |
| Kdenlive 20.08–22.12 document (version 1.0x) | `[unsupported_structure]`. Save it once in a recent Kdenlive first. |

### Why the open-project rule matters (observed, Kdenlive 26.08.2)

With a project open in Kdenlive, the file was changed on disk by another program. Kdenlive
showed no warning. After a change and a save in Kdenlive, the file contained Kdenlive's version
only: **the external change was silently lost**. Close the project in Kdenlive, or use
*File > Revert* after an external edit, before continuing to work in Kdenlive.

### Fidelity

- **Kept as read:** documents keep everything that was read: unknown elements and properties, attribute order, comments and whitespace.
- **Unchanged copies:** a no-op copy is byte-identical to Kdenlive's file. The only difference can be `<a></a>` written as `<a/>`, which is the same XML.
- **Verification:** before writing, the serialized bytes must parse back to the same canonical XML (C14N) as the edited document. After writing, the file must load as a Kdenlive project.
- **Moving a copy:** when the destination folder differs, relative media, proxy, LUT and luma references are rewritten so they still resolve (`rebased_paths`), and `root` is set to the new folder. A copy written two or more levels away uses absolute paths. Verified: a copy moved to another folder opens in Kdenlive 26.08.2 without a missing-media dialog.

### Filesystem scope

Paths must be absolute. The server reads and writes only the paths given to it and the files
those projects reference, with the permissions of the user running it, and only inside the
allowed roots: the user's videos directory by default, or `KDENLIVE_MCP_ALLOWED_ROOTS`
(`[path_not_allowed]` otherwise). `diagnose_environment` reports the effective roots in
`filesystem`.

## Editing tools: shared rules

Every editing tool reads `path`, applies the change in memory and writes the result to
`destination` with the rules of `save_project_copy`:

- **Copies by default.** Writing over the source needs `overwrite` and `expected_sha256`.
- **Conflict detection** before writing.
- **Atomic write** with a backup when replacing an existing file.
- **Path rebasing** when the destination is in another folder.

The result's `saved.changes` lists what was modified. Only Kdenlive 23.04+ documents
(version 1.1) are edited.

## `add_media_to_bin(path, media_paths, destination, …)`

Adds up to 50 audio or video files to the bin. They are not placed on the timeline.

- **Clip data:** MLT computes it under the *project's own profile* (written to a temporary MLT profile file, so custom profiles work too). Kdenlive metadata is added as in `create_project`.
- **Duplicates:** a file already in the bin (compared by resolved path, through proxies to the original) is reported as `existing` with its clip id and is not duplicated. Kdenlive does the same.
- **References:** stored relative to the project root when inside it, absolute otherwise.

## `add_track(path, kind, destination, name=None, sequence=None, …)`

Adds an empty track, in Kdenlive's track order (audio tracks below video tracks):

- **Video:** on top of all tracks; the new label is `V<n+1>`.
- **Audio:** below all audio tracks; the new label is `A<n+1>`. Every track moves up one MLT index, and the server renumbers the internal and user transitions (`a_track`/`b_track`), the clip groups, and the active, audio and video target tracks. Clips keep their track, timing and ids.

### How the track is built

The structure is copied from Kdenlive 26.08.2's own tracks:

- **Tractor:** two playlists, the second one for same-track mixes.
- **Audio extras:** `kdenlive:audio_track` and three disabled internal filters (volume, panner, audiolevel).
- **Internal transition:** one per track, `mix` for audio and `qtblend` for video.
- **Name:** stored in `kdenlive:track_name`.

### Verification

`research/lab/verify_roundtrip.py`: a copy of a 26.08.2 project with two media added, a named
video track and a named audio track (Unicode name) opened in Kdenlive 26.08.2 without dialogs.
After Kdenlive's save, these were identical:

- track labels, kinds and names;
- clips;
- bin media;
- the per-track internal transitions.

### Limits

- **Track positions:** other positions (a track between existing ones) are not supported yet.
- **Track properties:** track locking, muting and hiding are not set by these tools.

## Timeline edits: `insert_clip`, `move_clip`, `trim_clip`, `split_clip`, `remove_clip`

| Tool | Effect |
|---|---|
| `insert_clip(bin_id, track, start, source_in=0, source_out=None, audio_track="A1")` | Places a bin clip at `start`. An audio+video clip on a video track also gets its audio on `audio_track` (pass `null` for video only), grouped like Kdenlive. |
| `move_clip(clip, revision, start, track=None)` | Moves a clip, optionally to another track of the same kind. |
| `trim_clip(clip, revision, start=None, end=None)` | New timeline bounds `[start, end)`; the source in/out follow, like resizing in Kdenlive. |
| `split_clip(clip, revision, at)` | Razor cut at timeline frame `at` (`start < at < end`). |
| `remove_clip(clip, revision)` | Removes the clip and leaves a gap. The bin clip stays. `clips` lists the neighbours that were trimmed back to a cut (only when a mix was removed, see below). |

### Rules

- **Units and references:** frames at the project rate; timeline `[start, end)`, source in/out inclusive. Clips are referenced by `ref` (`track:layer:entry`) from `get_timeline`, together with `revision`, the `file.sha256` returned by that same call. If the file changed since, the edit is refused (`[stale_revision]`), because refs could point at another clip.
- **No ripple, no overwrite:** neighbours never move, and any overlap is refused (`[overlap]`). The only exception is removing a clip that is part of a crossfade: its partner is trimmed back to the original cut (see below). Trimming and extending are limited by the source length.
- **Groups:** a clip in a Kdenlive audio/video group (`AVSplit`) is moved, trimmed, split and removed together with its counterpart, as Kdenlive does by default. Groups are updated (a split creates a group for the new right halves).
- **Kdenlive's layout:** like Kdenlive 26.08, each clip instance on the timeline gets its own `<chain>`, a copy of the bin clip. On AV media, `set.test_audio` or `set.test_image` disables the unused half.

### Tracks with crossfades (same-track mixes)

Kdenlive overlaps two clips during a mix: the right clip is on the track's second layer, and
both are extended into their handles. Clips that are not part of a mix are edited as usual. A
mixed clip keeps its mix:

| Operation | On a mixed clip |
|---|---|
| `insert_clip`, `move_clip` of other clips | allowed where both layers are free (`[overlap]` otherwise) |
| `move_clip` | refused (`[unsupported_structure]`, names the mix) |
| `trim_clip` | the side without a mix only, and the clip must still extend at least one frame beyond the mix (`[unsupported_structure]`). Grouped audio and video move their edges by the same amounts, even when only one of them is mixed |
| `split_clip` | allowed outside the mix (`[unsupported_structure]` inside it); each half keeps the mix it touches |
| `remove_clip` | removes the clip **and its mixes**; each partner is trimmed back to the original `cut`, like deleting the mix in Kdenlive (`saved.changes` says so) |

A clip left on the second layer without a mix goes back to the first one, as in Kdenlive.
Verified: Kdenlive-made mixes (`tests/fixtures/kd26/kd26-mixes.kdenlive`) edited by each
operation reopen in Kdenlive 26.08.2 without dialogs, and Kdenlive's own save keeps clips,
groups, mixes and layers identical. The format is described in
[research/mixes.md](research/mixes.md).

### Refused (`[unsupported_structure]`)

- same-track transitions that are not Kdenlive mixes of two clips;
- moving a clip that is part of a mix;
- groups other than audio/video pairs;
- splitting, or trimming the start of, a clip whose effects are animated (keyframes would shift).

### Verification

`research/lab/verify_roundtrip.py` checked a montage built on a 29.97 fps Kdenlive 26.08.2
project in five steps:

1. insert an AV clip;
2. insert a sub-range of a WAV with a gap;
3. split the first clip;
4. move the right half (its audio followed);
5. trim and remove.

The step files after the split, the move and the final removal open in Kdenlive 26.08.2 without
dialogs. After Kdenlive's save, these are identical: tracks, clips, bin media, internal
transitions and **groups**. melt renders the result (180 frames).
