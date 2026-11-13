---
name: kdenlive-editing
description: How to edit Kdenlive projects with the kdenlive MCP tools - planning the timeline in frames, chaining edits in place, checking every brief item before rendering, and reporting honestly. Use for any Kdenlive editing, rendering or repair request.
---

# Editing with the kdenlive tools

## 1. Read before you plan

- `probe_media` every input once (frame rate, length, audio or not). Read any subtitle file you are given.
- For an existing project: `inspect_project`, `get_timeline`, `list_project_media`, `list_markers`, `list_subtitles`.
- Turn the brief into a numbered checklist, one line per requirement, with the target in **frames** at the project rate (timeline `[start, end)`, source in/out inclusive).

## 2. Plan the timeline on paper

- Write the planned start and end of every clip, mix, title, subtitle and guide.
- Crossfades (`add_mix`) need overlap material: trim shots with handles (source in/out inside the media), not at the media's first/last frame.
- Put pictures that must stay visible over others (cutaways, titles) on a track above. Create the tracks you need first (`add_track`).

## 3. Edit: one working file, chained in place

- Create the project once (`create_project`) or make one working copy (`save_project_copy`).
- Then edit **in place**: `destination` = `path`, `overwrite=true`. Each result gives `saved.sha256`: use it as the next `revision`, and use the clip refs it returns. Do not re-read the timeline between edits unless a ref is unknown.
- Do not create one file per step.
- Order: clips → trims/moves → mixes → effects and audio levels/fades → titles → subtitles (`import_subtitles` from a file) → guides.
- When a tool refuses, read the bracketed code and the hint, fix the cause, retry once. `[stale_revision]` means: read the timeline again.

## 4. Check every item before rendering

- `get_timeline`, `list_markers`, `list_subtitles`, `list_project_media`: tick each checklist line with the numbers you read (frames, dB, counts). Fix what does not match.
- No missing media.

## 5. Render and verify

- `render_project` (or `start_render` + `get_render_job` for long ones). The server validates streams and duration: report its `validation`.
- You cannot see or hear the render. Say what you checked from the project and the validation, and what only a person can confirm (look and sound).

## 6. Report

- One line per brief item: done (with the numbers) or not done (why).
- Mention files written besides the project and the render (backups are automatic).
