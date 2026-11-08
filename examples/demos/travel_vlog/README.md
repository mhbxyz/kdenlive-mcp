# Demo 1 — Travel vlog

A three-day trip (Lisbon, Porto, Algarve) told in twelve 3-second shots. The assistant:

1. builds the montage: 12 shots on V1, **crossfaded** one into the next (11 mixes of 10 frames, chained across Kdenlive's two layers);
2. places a **voice-over** per chapter on A1, one second into it;
3. imports the voice-over **subtitles** (`voice-over.srt`, generated with the media and timed for voice-overs one second into each chapter);
4. lays a **music bed** on A2 over the whole vlog, **ducks it under each voice-over** by splitting it at the voice boundaries and setting a level per piece (−20 dB under voices, −8 dB elsewhere), with a fade-in and a 2-second fade-out (`split_clip`, `set_clip_volume`, `set_clip_fades`);
5. adds an **intro** and an **outro title** on V2;
6. adds a **guide per chapter**, each with its own category;
7. **renders in 1080p** (MP4-H264/AAC).

Media are synthetic: coloured 1920×1080 25 fps shots with a label, espeak-ng voices (tones of the
same length when espeak-ng is absent), a sine music bed.

## Files

| File | Role |
|---|---|
| `prompt.md` | What to ask an assistant connected to `kdenlive-mcp` |
| `make_media.py` | Generates the 12 shots, 3 voices and the music |
| `run_demo.py` | Deterministic MCP client: plays the scenario, renders, checks |

## Checks

- 11 crossfades on V1; each music piece at −20 dB exactly under a voice-over and −8 dB elsewhere, with a fade-in and a 2 s fade-out; the voice-overs where the subtitles expect them; 3 chapter guides at the chapter starts, each in its own category;
- a 1920×1080 render as long as the timeline;
- each chapter's colour in the middle of the chapter;
- intro title in the top of the frame at 1 s (gone at 4 s), outro title 1 s before the end;
- a subtitle during the second voice-over and none just after it;
- the music fades out (last 0.25 s at least 6 dB below the end of chapter 2).
