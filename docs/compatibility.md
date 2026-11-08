# Compatibility

## Support status (v0.1)

Statuses are defined in [docs/versioning.md](versioning.md#external-software-support).

| Component | tested | supported | untested | unsupported |
|---|---|---|---|---|
| Kdenlive | 26.08.2 (everything); 23.08.5 (reading, editing, melt rendering) | 23.04 – 26.08 for reading and editing (document version 1.1) | other versions | `kdenlive --render` on 23.08 (refused with `[unsupported_kdenlive]`); editing documents older than 1.1 |
| MLT | 7.42.0, 7.22.0 | 7.x | — | — |
| FFmpeg / ffprobe | n9.0.2, 6.1.1 | ≥ 6 | older | — |
| Python | 3.11, 3.12, 3.13, 3.14 | — | 3.15+ | ≤ 3.10 |
| Linux | CachyOS (rolling), Ubuntu 24.04 (CI) | other distributions | Flatpak/Snap Kdenlive through wrappers | — |
| macOS, Windows | — | — | — | not supported |
| MCP clients | see [docs/clients.md](clients.md) | standard stdio MCP clients | desktop/IDE variants | cloud-only clients without a local bridge |

## Tested versions

| Environment | Kdenlive | MLT | FFmpeg | Python | What is exercised |
|---|---|---|---|---|---|
| Local reference (CachyOS, 2026-11-08) | 26.08.2 | 7.42.0 | n9.0.2 | 3.11–3.14 | everything (see below) |
| CI `Integration` job (Ubuntu 24.04 packages) | 23.08.5 | 7.22.0 | 6.1.1 | 3.13 | real-tool tests: diagnostics, ffprobe probing, creation and edits with melt, melt rendering of every fixture. `kdenlive --render` is absent in 23.08, so the server refuses with `[unsupported_kdenlive]`, which is also tested. |
| CI test matrix | — | — | — | 3.11, 3.12, 3.13, 3.14 | everything that needs no external program, on all fixtures |

Project documents read: Kdenlive document version **1.1** (Kdenlive ≥ 23.04, written by
23.04–26.08.2 in the fixtures) and **1.04** (Kdenlive 22.x, read-only and best effort). Editing
requires 1.1. Other Kdenlive versions are **untested**.

### What was verified with Kdenlive 26.08.2 itself

Verified locally with `research/lab/verify_roundtrip.py`: the project opens with no dialog, and after
Kdenlive's own *Save As*, tracks, clips, bin media, internal transitions and groups are identical.

| Project | Opens + round-trips in Kdenlive | `kdenlive --render` | melt render |
|---|---|---|---|
| Created by `create_project` (29.97/25 fps, AV and audio-only) | ✅ | ✅ | ✅ |
| Copy moved to another folder (`save_project_copy`) | ✅ | ✅ | ✅ |
| Media and named tracks added (`add_media_to_bin`, `add_track`) | ✅ | — | ✅ |
| Montage: insert, split, grouped move, trim, remove | ✅ | ✅ | ✅ |
| Kdenlive-made project with subtitles | ✅ | ❌ Kdenlive crashes (SIGSEGV), reproduced without this server (#16) | ✅ |

## Fixture corpus

Fixtures live in `tests/fixtures/`. Provenance is in `tests/fixtures/PROVENANCE.md`. The
expected reading of each fixture is in `tests/fixtures/compat.json`.

| Feature | Fixtures |
|---|---|
| 29.97 fps (30000/1001) | `kd26/*` |
| Audio and video tracks, AV groups, gaps | `kd26/kd26-ntsc-timeline`, `kd26/generated-montage`, `upstream/clip-ids*` |
| Split / trim / sub-range / named tracks / 6 tracks | `kd26/generated-montage` |
| Unicode media, track names and subtitle text | `kd26/*` |
| Subtitles (ASS) | `kd26/kd26-subtitle` |
| Effects, keyframes | `upstream/test-keyframes*`, `upstream/test-nesting-effects*` |
| Multiple and nested sequences | `upstream/test-nesting*` |
| Same-track mixes, document 1.04 | `upstream/test-mix`, `upstream/av` |
| Missing effect asset (LUT), missing proxy | `upstream/test-missing`, `upstream/missing-proxy` |
| Empty timeline | `kd26/kd26-ntsc` |
| Titles | **not covered yet**: creating one requires the Kdenlive title editor (#15) |

`tests/test_compat.py` checks:

- **Every fixture:** it reads exactly as recorded (version, frame rate, sequences, duration, clips, tracks, missing items).
- **Every editable fixture:** it survives a no-op save, with canonical XML identical.
- **Every complete fixture, with real tools:** melt renders it with the expected number of frames (Kdenlive 26.08 stores one extra frame, which is tolerated).
- **Kdenlive 26.08.2 fixtures, where Kdenlive has command-line rendering:** `kdenlive --render` renders them; the subtitle crash is an expected failure.

### Regenerating

- **Synthetic media:** `uv run python scripts/make_fixtures.py media DIR`. FFmpeg `lavfi` sources, bit-exact flags, no metadata. Bytes may differ across FFmpeg versions; the committed media are the reference.
- **Montage:** `uv run python scripts/make_fixtures.py montage`. It is rebuilt with this package's own editing functions, so review the diff when the editing code changes.
- **Kdenlive-made projects:** recreate them with the research lab (`research/lab/README.md`) and sanitize them as described in `PROVENANCE.md`.

## Adding a Kdenlive version to the matrix

1. **Install the version** (distribution, AppImage, …) and point `KDENLIVE_MCP_KDENLIVE` and `KDENLIVE_MCP_MELT` at it.
2. **Run the suite:** `uv run kdenlive-mcp diagnose`, then `uv run pytest -m integration`.
3. **Run the lab check** on the generated fixtures: `uv run python research/lab/verify_roundtrip.py tests/fixtures/kd26/*.kdenlive`, with `KD_ARGS`/`PATH` pointing at that Kdenlive.
4. **Add fixtures it wrote:** open or re-save a few fixtures in it (lab or GUI), sanitize them, add them under `tests/fixtures/<version>/`, and record them in `compat.json` and `PROVENANCE.md`.
5. **Update the tables** above with what passed and what did not. Record a failure as a limitation with evidence; never claim support it does not have.

## Manual GUI checks

CI cannot open Kdenlive's interface. The lab automates *open → save → compare* locally, but
interactive editing must be checked by hand. The checklists are kept in the issues (#8, #10, #11)
and in the release notes as known limitations. For every new Kdenlive version, open each
`kd26/*` fixture and run through this checklist:

- [ ] Opens without a dialog; bin thumbnails appear; the profile in *Project Settings* matches `compat.json`.
- [ ] Timeline matches `get_timeline`: tracks and names, clip positions, gaps, groups (moving one half moves the other).
- [ ] Trim, move, undo and redo work; save, close, reopen keeps the edits.
- [ ] GUI render of `generated-montage` has the expected length and cuts.
- [ ] `kd26-subtitle` shows the subtitle "Bonjour é ✓" at 00:00:02.
