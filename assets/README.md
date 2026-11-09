# Demo media

`kdenlive-mcp-demo.mp4` (1920×1080, sound) and its silent preview `kdenlive-mcp-demo.webp`
(used by the main README) show one unscripted session, recorded on 2026-11-09.

## What was recorded

- **Client:** Claude Code 2.1.319, Claude Opus 5.5 (high effort), with only this server
  (`--strict-mcp-config`, `uvx --from mcp-kdenlive==0.3.0 kdenlive-mcp`). Shell, file writing
  and web tools were disabled: every edit went through the MCP tools.
- **Kdenlive:** 26.08.2 on CachyOS, on a virtual X display, with private settings and caches.
  Each time Claude saved, a copy of the project was opened in a fresh Kdenlive, which replaced
  the previous one on screen. Kdenlive never had the edited file itself open.
- **Prompt:** typed as shown in the video, in one go. Claude worked for 2 min 25 s without
  further input.

```text
Cut a trailer of about 30 seconds from the Big Buck Bunny shots in ~/kdenlive-demo/footage
and save it as ~/kdenlive-demo/trailer.kdenlive:
- the six shots in order, about 5 s each, with 1 s crossfades
- opening title "BIG BUCK BUNNY" over the first shot
- mute the shots, use music.m4a as the soundtrack, fade it in and out
- brighten the dark rock shot, fade the last shot to black
- import captions.srt and add a guide at each shot
- closing title "Big Buck Bunny (c) Blender Foundation - CC BY 3.0"
Then render trailer.mp4 and check it.
```

## Editing

The session is shown at 2× while the prompt is typed and 4× afterwards. Nothing was cut out
of it. Then the video shows the rendered `trailer.mp4` exactly as produced, with its
sound. The opening and closing cards were added afterwards.

## Credits

Footage and music: *Big Buck Bunny*, © 2008 Blender Foundation,
[peach.blender.org](https://peach.blender.org), licensed under
[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). The six shots and the music are
excerpts of the original film, re-encoded. Fonts: Fira Sans and Hack (SIL Open Font License /
MIT).
