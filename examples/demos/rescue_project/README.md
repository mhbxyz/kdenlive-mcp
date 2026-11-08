# Demo 3 — Rescue of a project made by Kdenlive

A colleague's project, made and saved by **Kdenlive 26.08.2** itself, with two crossfades
(Kdenlive's *Mix Clips*) on video and audio. Its media folder was renamed (`media/` →
`footage/`), so it opens with missing media. The assistant:

1. **diagnoses** it: `inspect_project` (Kdenlive version, profile), `list_project_media` (three files missing);
2. **relinks** the three files to the renamed folder in one call (`relink_media`);
3. reads the timeline and its **crossfades** (`get_timeline`: mixes at 30–60 and 70–90 on V1 and A1);
4. tries to trim the first clip into the crossfade, which the server **refuses** without writing anything (`[unsupported_structure]`);
5. **shortens the first crossfade** (`remove_mix`, then `add_mix` of 12 frames: Kdenlive's layout, with the middle clip on the second layer and already in the next crossfade, is kept);
6. lowers the middle clip's sound by 6 dB, adds a **fade to black** and an **audio fade-out** on the last clip, and two **guides**;
7. **renders with melt** (`engine="melt"`), the route for Kdenlive projects when `kdenlive --render` is not available or not wanted.

The project comes from the test fixtures (`tests/fixtures/kd26/kd26-mixes.kdenlive`, synthetic
CC0 media, see `tests/fixtures/PROVENANCE.md`); `make_media.py` copies it with its media in
`footage/`.

## Files

| File | Role |
|---|---|
| `prompt.md` | What to ask an assistant connected to `kdenlive-mcp` |
| `make_media.py` | Copies the Kdenlive-made project and its media (renamed folder) into `WORKDIR` |
| `run_demo.py` | Deterministic MCP client: plays the scenario, renders, checks (exit 0 when all pass) |

## Checks

- the three missing files are diagnosed, relinked, and no file is missing afterwards;
- the trim into a crossfade is refused;
- the crossfades are at 34–46 and 70–90 after the retouch;
- the render lasts as long as the timeline, shows the picture (not black: media found), has
  sound, ends on black and with the sound faded out (6 dB quieter than the start).
