# Demo 4 — Vertical short (9:16, 30 fps)

A social-media short made from three **landscape** shots (beach, city, night):

1. a new **vertical 1080×1920, 30 fps** project (`create_project`, profile `vertical_hd_30`);
2. each shot **cropped around its centre** to fill the vertical frame instead of being letterboxed (`add_effect crop center=true`);
3. **crossfades** between the shots (`add_mix`, 20 frames), with 15-frame handles left on each shot;
4. the night shot **graded darker** (`gamma 0.6`);
5. a two-line **title** at the top for the first two seconds, on a new video track (`add_track`, `add_title`);
6. a **music bed** on A2, cut to the length of the edit, lowered by 8 dB, faded in and **faded out** (`set_clip_volume`, `set_clip_fades`);
7. a **render** with the default preset (MP4-H264/AAC).

## Files

| File | Role |
|---|---|
| `prompt.md` | What to ask an assistant connected to `kdenlive-mcp` |
| `make_media.py` | Generates the synthetic media with FFmpeg (`python make_media.py WORKDIR`) |
| `run_demo.py` | Deterministic MCP client: plays the scenario over stdio, renders, checks (`python run_demo.py WORKDIR`, exit 0 when every check passes) |

## Checks on the render

- 1080×1920 frame; duration equal to the timeline's;
- the top of the frame is not black (cropped, not letterboxed); the first shot is visible;
- a title in the top 30 % of the frame at 0.5 s, gone at 3 s;
- the graded night shot is darker than the city shot;
- the music fades out (the last 0.25 s is at least 6 dB quieter than the middle).

`tests/e2e/test_demos.py` runs this script in the CI integration job.
