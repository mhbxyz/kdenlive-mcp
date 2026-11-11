# Compatibility

## Support status (v0.1)

Statuses are defined in [docs/versioning.md](versioning.md#external-software-support).

| Component | tested | supported | untested | unsupported |
|---|---|---|---|---|
| Kdenlive | 26.08.2 (everything); 23.08.5 (reading, editing, rendering with the melt engine); 24.12.3 and 25.04.3 (reading, editing and rendering projects they saved; rendering this server's and 26.08's projects, see below); 26.08.3 (rendering) | 23.04 – 26.08 for reading and editing (document version 1.1); rendering with `kdenlive --render` from 24.02, with melt before | other versions | `engine="kdenlive"` before 24.02 (refused with `[unsupported_kdenlive]`); editing documents older than 1.1 |
| MLT | 7.42.0, 7.22.0 | 7.x | — | — |
| FFmpeg / ffprobe | n9.0.2, 6.1.1 | ≥ 6 | older | — |
| Python | 3.11, 3.12, 3.13, 3.14 | — | 3.15+ | ≤ 3.10 |
| Linux | CachyOS (rolling), Ubuntu 24.04 (CI) | other distributions | Snap Kdenlive through wrappers | — |
| AppImage (download.kde.org) | 24.12.3, 25.04.3, 26.08.3 through `KDENLIVE_MCP_KDENLIVE`: detection and rendering | — | other releases | — |
| Flatpak (Flathub `org.kde.kdenlive`) | 26.08.3 (user installation, with its melt 7.43.0 and FFmpeg 9.0.2): detection, probing, creation, both render engines | system installation | — | files in `/tmp`, `/usr`, `/etc`… (the sandbox cannot see them; refused with `[invalid_path]`) |
| macOS, Windows | — | — | — | not supported |
| MCP clients | see [docs/clients.md](clients.md) | standard stdio MCP clients | desktop/IDE variants | cloud-only clients without a local bridge |

## MCP protocol and SDK

| | Supported |
|---|---|
| MCP Python SDK | `mcp>=2.3,<3` (tested with 2.3.0) |
| Protocol revisions negotiated by the SDK | 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25 (initialize handshake) and 2026-07-28 |
| Transport | stdio only |

The server does not pin a protocol revision: the SDK negotiates the newest one both sides know.
A new major version of the SDK is a compatibility change for this server and is released as a
new minor version (from 1.0: as decided in the changelog). Tested clients and versions are in
[clients.md](clients.md).

## Other Kdenlive releases (#66)

Checked on 2026-11-08 and 2026-11-11 with the official AppImages from download.kde.org (SHA-256
verified), pointed to by `KDENLIVE_MCP_KDENLIVE`:

| Kdenlive | Detection | `kdenlive --render` of projects written by this server and by Kdenlive 26.08.2 | Projects saved by this release |
|---|---|---|---|
| 24.12.3 (AppImage) | ✅ version, `--render` detected | ✅ montage, crossfades, AV pair: picture and sound | ✅ read, edited, rendered with melt (#86) |
| 25.04.3 (AppImage) | ✅ version, `--render` detected | ✅ same | ✅ same (#86) |
| 26.08.3 (AppImage) | ✅ version, `--render` detected | ✅ same | — (same format as 26.08.2) |

Effects and crossfades written by the server (#14) render the same with both releases. Finding:
Kdenlive's *default* fade from/to black (an alpha fade) renders as no fade with 24.12.3 (and with
MLT 7.22's melt), even in a file saved by Kdenlive 26.08.2; the server therefore writes the
brightness form (*Fade to Black* checked), which renders with 24.12.3, 25.04.3, 26.08.2 and melt.

`tests/test_compat.py::test_other_kdenlive_releases_render_fixtures` repeats the rendering checks with any
binaries listed in `KDENLIVE_MCP_TEST_KDENLIVE` (`:`-separated; skipped when unset). AppImages are
supported through `KDENLIVE_MCP_KDENLIVE`; they bundle their own MLT, used by `kdenlive --render`.

Projects *saved* by 24.12.3 and 25.04.3 (#86) were made by driving the AppImages' GUI on a
private X display (`research/lab/kdx11.py`; they register on neither the accessibility bus nor the
session D-Bus, so the AT-SPI lab cannot drive them). Crossfades, subtitles, a montage, keyframed
effects, guides and a title read exactly like the 26.08.2 files they came from (tracks, clips,
mixes, effects, guides, markers, subtitle events), survive a no-op save and can be edited
(`tests/test_older_saves.py`). Differences found in the files:

- **Project length with subtitles:** 24.12 and 25.04 end the project's output (the project
  tractor) at the last subtitle when it ends after the last clip; 26.08 ends it one frame after
  the last clip. melt and `kdenlive --render` render that length, so a render of such a project
  is longer than the clips. Since #86 the server checks a render against the project's output
  length. An edit by the server sets the output back to the clips' end, like 26.08.
- **Title font name:** 25.04.3 (AppImage) stored its default title font as `Sans Serif`, the
  name its bundled fontconfig resolves; 26.08.2 writes the family (`Noto Sans`).
- **Lab only:** the 24.12.3 AppImage kept *Add Title Clip* disabled and reported title fonts as
  missing (see the font note below), so its title fixture comes from the 25.04.3 one.
- Everything else (document version 1.1, sequences, mixes, groups, effects, guides JSON, ASS
  subtitles) is written the same way.

Titles (#15): the 25.04.3 AppImage renders the server's titles (centred, lower third, positioned).
The 24.12.3 AppImage renders **no title at all** on this machine, also from the file saved by
Kdenlive 26.08.2: its bundled fontconfig rejects the host's `/etc/fonts` configuration (warnings in
the render log), so it has no font to draw with. This was not reproduced elsewhere.

## Headless rendering with MLT 7.22 (#90)

MLT 7.22's Qt module (`kdenlivetitle`, `qtblend`) needs an X11 display, even with
`QT_QPA_PLATFORM=offscreen`; MLT 7.42 does not. Reproduced in `ubuntu:24.04` (Docker). The melt
engine passes the display variables through and fails clearly without one; the CI
`Integration` job runs under `xvfb-run`, and an e2e test composites two video tracks with the
`auto`, `melt` and `kdenlive` engines (on CI's Kdenlive 23.08 the first two run melt and the
third is skipped; locally Kdenlive 26.08.2 runs it).

## Tested versions

| Environment | Kdenlive | MLT | FFmpeg | Python | What is exercised |
|---|---|---|---|---|---|
| Local reference (CachyOS, 2026-11-08) | 26.08.2 | 7.42.0 | n9.0.2 | 3.11–3.14 | everything (see below) |
| CI `Integration` job (Ubuntu 24.04 packages) | 23.08.5 | 7.22.0 | 6.1.1 | 3.13 | real-tool tests: diagnostics, ffprobe probing, creation and edits with melt, melt rendering of every fixture. `kdenlive --render` is absent in 23.08: `render_project` uses the melt engine there (end-to-end tests render through it), and `engine="kdenlive"` is refused with `[unsupported_kdenlive]`, which is also tested. |
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
| Kdenlive-made crossfades, then trims, splits, removals and moves around them (#61) | ✅ | ✅ (e2e) | ✅ (e2e) |
| Effects (crop, brightness, greyscale, sepia, gamma, fades from/to black) and crossfades added, updated and removed by the server (#14) | ✅ (values compared; Kdenlive rewrites keyframe positions as timecodes) | ✅ (e2e) | ✅ |
| Titles (centred, lower third, positioned; then trimmed, split, moved) added by the server (#15) | ✅ (text and style compared) | ✅ | ✅ (e2e) |
| Subtitles imported from SRT and WebVTT (new, or replacing a Kdenlive-made track, with an offset) (#16) | ✅ (events compared) | — (crashes on subtitles, #16) | ✅ (e2e) |
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
| Subtitles (ASS) | `kd26/kd*-subtitle` |
| Saved by Kdenlive 24.12.3 and 25.04.3 | `kd26/kd24.12-*`, `kd26/kd25.04-*`, `upstream/test-keyframes.resaved-24.12.3`, `…-25.04.3` |
| Guides with categories and Unicode comments | `kd26/kd*-guides` |
| Effects, keyframes | `upstream/test-keyframes*`, `upstream/test-nesting-effects*` |
| Multiple and nested sequences | `upstream/test-nesting*` |
| Same-track mixes (crossfades) made by Kdenlive 26.08.2, video and audio | `kd26/kd26-mixes` |
| Same-track mixes, document 1.04 | `upstream/test-mix`, `upstream/av` |
| Missing effect asset (LUT), missing proxy | `upstream/test-missing`, `upstream/missing-proxy` |
| Empty timeline | `kd26/kd26-ntsc` |
| Titles made in Kdenlive's title editor | `kd26/kd*-title` |

`tests/test_compat.py` checks:

- **Every fixture:** it reads exactly as recorded (version, frame rate, sequences, duration, clips, tracks, missing items).
- **Every editable fixture:** it survives a no-op save, with canonical XML identical.
- **Every complete fixture, with real tools:** melt renders it with the expected number of frames (Kdenlive 26.08 stores one extra frame, which is tolerated; `render_frames` in `compat.json` records the 24.12/25.04 subtitle projects, longer than their clips).
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
interactive editing must be checked by hand. The checklists are kept in the issues (#8, #10, #11,
#12, #13, #17, #61, #62; all linked from #67) and in the release notes as known limitations. For every new Kdenlive version, open each
`kd26/*` fixture and run through this checklist:

- [ ] Opens without a dialog; bin thumbnails appear; the profile in *Project Settings* matches `compat.json`.
- [ ] Timeline matches `get_timeline`: tracks and names, clip positions, gaps, groups (moving one half moves the other).
- [ ] Trim, move, undo and redo work; save, close, reopen keeps the edits.
- [ ] GUI render of `generated-montage` has the expected length and cuts.
- [ ] `kd26-subtitle` shows the subtitle "Bonjour é ✓" at 00:00:02.
