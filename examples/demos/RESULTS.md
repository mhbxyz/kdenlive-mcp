# Real-client runs

Each demo's `prompt.md` given to a real MCP client with `examples/demos/real_client.py` (not in CI).

- **Client:** Claude Code 2.1.319, non-interactive (`claude -p`, `--output-format stream-json`).
- **Model:** `claude-opus-5-5`.
- **Date:** 2026-11-08.
- **Server:** this branch's `kdenlive-mcp`, the only MCP server (`--strict-mcp-config`), with
  `KDENLIVE_MCP_ALLOWED_ROOTS` set to the demo's folder.
- **Machine:** CachyOS, Kdenlive 26.08.2, MLT 7.42.0, espeak-ng.
- **Client tools:** the server's tools were allowed. Bash, Write, Edit and web tools were
  disallowed, except in the vertical-short run, where Bash was not yet disallowed and its uses
  were refused by the client's permission prompt. Read, Glob, Grep and sub-agents stayed
  available.

"Server errors" are tool results flagged as errors by the server. Refusals by the client's own
permission system are listed separately.

Every project the client produced was then opened and saved by Kdenlive 26.08.2 in the GUI lab
(`research/lab/verify_roundtrip.py`): all four reopened without dialogs, timelines identical. One
frame of each render was looked at by a person.

## Summary

| Demo | Turns | Time | Cost | Server errors | Result |
|---|---|---|---|---|---|
| Travel vlog | 76 | 378 s | $2.85 | 0 | Done, but subtitles became title clips (see below) |
| Interview (edit + "I moved the media") | 31 + 8 | 119 + 31 s | $1.16 + $1.39 | 1 (recovered) | Done except the voice levels |
| Rescue of a Kdenlive project | 36 | 122 s | $0.92 | 0 | Done as asked |
| Vertical short | 45 | 173 s | $1.40 | 0 | Done; it verified the picture on rendered stills |

## Travel vlog

Run with an earlier version of the prompt, which gave the subtitle texts instead of an `.srt`
file.

**Tool calls:**

- `add_mix` ×17, `insert_clip` ×15, `set_clip_volume` ×7, `probe_media` ×6, `split_clip` ×6,
  `add_title` ×5, `add_marker` ×3, `set_clip_fades` ×2, `get_timeline` ×2;
- one each of `create_project`, `add_media_to_bin`, `trim_clip`, `move_clip`,
  `list_project_media`, `list_render_presets`, `render_project`, `diagnose_environment`.

**Result (read back):**

- 1050 frames (42 s at 25 fps) and a 1920×1080 render with sound.
- 12 shots with 11 crossfades of 12 frames each.
- Voice-overs one second into each day.
- Music ducked from −14 dB to −24 dB under each voice-over. It made the ramps with audio
  crossfades between music pieces, something the deterministic script does not do.
- Fade-in and 2 s fade-out on the music.
- Intro and outro titles; 3 day guides; no missing media.

**Friction:**

- **Subtitles from text:** `import_subtitles` needs a file, and the client had no tool to write
  one. It put the three sentences as title clips at the bottom instead, and said so plainly.
  The prompt now ships `voice-over.srt` with the media. Creating subtitles from text is a
  missing capability (reported on #68).
- **Model error:** it said each edit left a backup, "about 50 in total". Rotation kept 5.
- **Misleading server warning:** edits warned that "Kdenlive is running and it could not be
  checked". The running Kdenlive processes were command-line renders from the test suite,
  started at the same time. Fixed on this branch: `kdenlive --render` processes are no longer
  counted as open projects.

## Interview

Two prompts in one session: the edit, then, after the media folder was moved, "I moved the
media folder … fix the project, render it".

**Tool calls:**

- **Edit:** `probe_media` ×4, `add_marker` ×4, `insert_clip` ×3, `add_track` ×3,
  `add_title` ×2, `get_timeline` ×2, plus `create_project`, `add_media_to_bin`,
  `import_subtitles`, `list_subtitles`.
- **Fix and render:** `list_project_media` ×2, `relink_media`, `get_timeline`, `render_project`.

**Server error:** `add_track` in place without `expected_sha256` returned `[source_protected]`.
The client passed the revision and continued.

**Result:**

- Cutaways on a new track without their audio.
- Lower thirds bottom left, placed with `x`/`y` above the subtitles. It computed both heights.
- 3 subtitles; 4 guides.
- After the move: 4 missing media diagnosed and relinked, 0 missing afterwards.
- Render 8.64 s, 1920×1080, melt engine (chosen automatically because of the subtitles).

**Friction:**

- **Voice levels:** no tool measures loudness. The client sent a helper agent to run ffmpeg,
  which had no shell, then chose to leave the levels unchanged rather than guess. It said so.
  The deterministic script knows the recording gap (14 dB); a client cannot find it. Reported on
  #68 (a loudness reading, e.g. EBU R128, in `probe_media` or a dedicated tool).
- **Extra track:** `create_project` already makes two video tracks. The client added two more
  and left V2 empty, which it pointed out.

## Rescue of a Kdenlive-made project

**Tool calls:**

- **Inspection:** `inspect_project`, `list_project_media` ×2, `get_timeline` ×2, `probe_media`
  ×4, `list_markers`, `list_effects`, `diagnose_environment`.
- **Edits:** `relink_media`, `remove_mix`, `add_mix`, `set_clip_volume`, `add_effect`,
  `set_clip_fades`, `add_marker` ×2, `render_project` (melt).

**Result, exactly as asked:**

- Kdenlive 26.08.2 and the three missing files diagnosed; all relinked.
- First crossfade 12 frames (34–46) on video and audio; second one kept (70–90).
- Middle clip −6 dB; fade to black and audio fade-out on the last clip.
- 2 guides; render 4.35 s, 1280×720.

It also noticed that the stored `root` points to a folder that no longer exists, and that two
bin clips are unused.

**Friction:** none from the server.

## Vertical short

**Tool calls:**

- `create_project` (`vertical_hd_30`), `add_media_to_bin`, `trim_clip`, `insert_clip` ×3,
  `add_effect` ×4 (crop ×3, brightness), `add_mix` ×2, `add_title`, `set_clip_volume`,
  `set_clip_fades`;
- `render_project` ×2: the second one with an image-sequence preset, to look at stills with
  Read.

**Client refusals:** 7 Bash attempts (ffmpeg probes and measurements) refused by the client's
permission system. None reached the server.

**Result:**

- 270 frames (9 s at 30 fps), render 1080×1920.
- Shots cropped to fill the frame; 12-frame crossfades on video and audio; night shot darker.
- Two-line title at the top for 2 s.
- Music at −12 dB with 0.5 s and 1.5 s fades.

**Friction:**

- **Checking the result:** the client could not look at frames or measure audio directly.
  Rendering an image sequence worked around it for pictures. A frame-extraction or audio-level
  tool would help (reported on #68).

## Interface friction found (for #63 / #68)

1. **No subtitles from text:** `import_subtitles` only reads files. An assistant without a file
   tool cannot add subtitles it writes itself.
2. **No loudness measurement:** voice harmonisation and ducking levels are guesses.
3. **Checking the result is indirect:** stills come from an image-sequence render, and audio
   cannot be checked at all.
4. **`create_project` makes two video tracks:** this is Kdenlive's default, but it was not
   obvious. Its description now says so (this branch).
5. **Fixed on this branch:** command-line renders running alongside made edits warn about an
   open project.
