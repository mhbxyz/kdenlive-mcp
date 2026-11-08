# Demo 2 — Interview

A two-person interview: Sam asks a question (recorded far too quietly), Ana answers. The
assistant:

1. builds the edit: the question then the answer on V1/A1 (`create_project`, `add_media_to_bin`, `insert_clip`);
2. puts two **cutaways** (the hull, the tools) over the answer on V2, without their audio (`insert_clip … audio_track=null`);
3. adds **name titles** for both speakers on a new top track, top left: the bottom of the frame is kept for the subtitles (`add_track`, `add_title`);
4. imports the **transcript** as subtitles (`import_subtitles`, SubRip);
5. **harmonises the voices**: the question is raised by 14 dB (`set_clip_volume`);
6. adds **guides** at the question, the answer and each cutaway (`add_marker`);
7. the media folder is then **moved to an archive disk**; the assistant sees the missing media (`list_project_media`) and **relinks** them (`relink_media`, #17);
8. **renders** (MP4-H264/AAC; subtitles imply the melt engine under `auto`).

Speech is synthesised with espeak-ng when it is installed; otherwise tones of the same length
stand in (as in CI).

## Files

| File | Role |
|---|---|
| `prompt.md` | What to ask an assistant connected to `kdenlive-mcp` |
| `make_media.py` | Generates the two talking shots, two cutaways and `interview.srt` |
| `run_demo.py` | Deterministic MCP client: plays the scenario, moves the media, relinks, renders, checks |

## Checks

- 3 subtitles imported, 4 guides, 4 media missing after the move and none after relinking;
- render as long as the timeline; the question (grey), the hull cutaway (green) and the tools cutaway (blue) at their times;
- Ana's name title during her first 2 seconds only; a subtitle under the question;
- question and answer within 4 dB of each other (they were 14 dB apart).
