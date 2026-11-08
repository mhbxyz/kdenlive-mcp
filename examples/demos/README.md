# Demos

Four realistic editing jobs, each played two ways:

- by a **deterministic MCP client script** (`run_demo.py`), which talks to the real
  `kdenlive-mcp` server over stdio exactly like an assistant would, then checks the rendered
  file objectively (frame size, duration, colours at given times, text where expected, audio
  levels and fades). These scripts also run as end-to-end tests (`tests/e2e/test_demos.py`, CI
  integration job);
- by a **real assistant**, given the scenario's `prompt.md` (results in [RESULTS.md](RESULTS.md)).

| Demo | What it exercises |
|---|---|
| [travel_vlog](travel_vlog/) | 12 crossfaded shots, voice-overs, ducked music with fades, intro/outro titles, chapter guides, subtitles, 1080p render |
| [interview](interview/) | cutaways on an upper track, name titles, subtitles, harmonised voice levels, guides, media folder moved and relinked |
| [rescue_project](rescue_project/) | a project made by Kdenlive with crossfades and missing media: diagnosis, relinking, retouching around the crossfades, melt render |
| [vertical_short](vertical_short/) | 9:16 30 fps project, crop to fill, colour grade, crossfades, title, music fade-out |

```sh
uv run python examples/demos/travel_vlog/run_demo.py /tmp/vlog   # prints OK, exit 0
```

Requirements: `melt`, `ffmpeg`, `ffprobe` (and Kdenlive for its own render path); `espeak-ng`
optional (speech, otherwise tones). Media are generated with FFmpeg; nothing large is committed.
The rescue demo reuses a Kdenlive-made test fixture (`tests/fixtures/kd26`, synthetic CC0).

`demolib.py` holds the shared helpers: the MCP session (`connect`, `Session`, `Editor`), media
generation (`make_clip`, `make_tone`, `make_speech`) and render checks (`probe`, `region_rgb`,
`dominant`, `bright_box`, `mean_volume`).
