# Test fixture provenance

No fixture contains personal media or user projects.

## `upstream/` — Kdenlive test dataset (CC0-1.0)

Copied unchanged from `tests/dataset/` of the Kdenlive 26.08.2 source release
(`kdenlive-26.08.2.tar.xz`, download.kde.org). Upstream `REUSE.toml` declares
`tests/dataset/**` as `CC0-1.0`, © Jean-Baptiste Mardelle, Eric Jiang.

| Files | Notes |
|---|---|
| `av`, `clip-ids`, `test-mix`, `test-keyframes`, `test-nesting`, `test-nesting-effects`, `test-missing`, `missing-proxy` (`.kdenlive`) | Projects written by Kdenlive 22.07–24.01 (document versions 1.04 and 1.1). |
| `blue`/`green`/`red`/`yellow` `.mp4`/`.mpg`/`.png`, `mono.flac`, `stereo.flac` | Media referenced by those projects. |
| `*.resaved-26.08.2.kdenlive` | The matching upstream project, opened and saved by **Kdenlive 26.08.2** through the research lab (`research/lab/kdgui.sh`, isolated configuration). |

## `kd26/` — projects created by Kdenlive 26.08.2

Created in the research lab with Kdenlive 26.08.2, MLT 7.42.0 and default profile
`atsc_720p_2997` (HD 720p 29.97 fps). Clips were added with the D-Bus `addProjectClip`
and `addTimelineClip` methods and saved through the GUI file dialog.

| File | Content |
|---|---|
| `kd26-ntsc.kdenlive` | 3 bin clips (`media/ntsc.mp4`, `media/pal clip é.mp4`, `media/tone.wav`), empty timeline (2 audio + 2 video tracks). |
| `kd26-ntsc-timeline.kdenlive` | Same bin; `ntsc.mp4` placed at frame 0 (video on V1, audio on A2). |
| `kd26-subtitle.kdenlive` + `.kdenlive.ass` | `kd26-ntsc-timeline` with one subtitle ("Bonjour é ✓", 00:00:02–00:00:07) added by Kdenlive 26.08.2 through D-Bus `slotAddSubtitle`, then saved with *Save As*. Kdenlive wrote the ASS file. |
| `kd26-mixes.kdenlive` | Built by `scripts/make_fixtures.py mixes`: three adjacent AV clips of `ntsc.mp4` with handles placed by this package on `kd26-ntsc`, then **Kdenlive 26.08.2 itself** added the crossfades (headless GUI lab: *Select All*, *Mix Clips*, *Save As*). Two same-track mixes on V1 and on A1 (`luma` and `mix`), a clip on the secondary layer, AV groups. `root` and `browserurl` sanitized. |
| `generated-montage.kdenlive` | **Not written by Kdenlive.** Built by `scripts/make_fixtures.py montage` with this package's editing functions, starting from `kd26-ntsc-timeline`. It contains 2 named tracks, gaps, a split, a grouped move, a trim, sub-ranges and a video-only clip. Verified to open and round-trip in Kdenlive 26.08.2. |
| `media/*` | Synthetic media generated with FFmpeg `lavfi` (`smptebars` 30000/1001 fps, `testsrc2` 25 fps, `sine`). See `scripts/make_fixtures.py media`. |

## Projects saved by Kdenlive 24.12.3 and 25.04.3 (#86)

Made on 2026-11-11 with the official AppImages `kdenlive-24.12.3-x86_64.AppImage` and
`kdenlive-25.04.3-x86_64.AppImage` from download.kde.org (SHA-256 checked against the `.sha256`
files next to them). Each AppImage was extracted and run on a private X display with private
XDG directories and session bus, driven by keyboard and mouse (`research/lab/kdx11.py`), on a
copy of the fixture folder. Each file was saved with *File > Save As* into the same folder.
`kdenlive:docproperties.kdenliveversion` records the release (MLT 7.30.0 for 24.12.3, 7.33.0
for 25.04.3).

| File (`<v>` = `24.12`, `25.04`) | Source and steps |
|---|---|
| `kd26/kd<v>-mixes.kdenlive` | `kd26-mixes.kdenlive` opened, saved. |
| `kd26/kd<v>-subtitle.kdenlive` + `.kdenlive.ass` | `kd26-subtitle.kdenlive` opened, saved. The release wrote the ASS file. |
| `kd26/kd<v>-montage.kdenlive` | `generated-montage.kdenlive` opened, saved. |
| `kd26/kd<v>-guides.kdenlive` | `kd26-ntsc-timeline.kdenlive` opened, two guides added with *Add Guide* (`G`): frame 10 « Intro é » (category 1), frame 45 « Chapter 2 » (category 2), saved. |
| `kd26/kd25.04-title.kdenlive` | `kd26-ntsc-timeline.kdenlive` opened, *Project > Add Title Clip*, one text item « Titre 25.04 é », *Create Title*, clip dragged to V2 at frame 0, saved. 25.04.3 stored the font as `Sans Serif`. |
| `kd26/kd24.12-title.kdenlive` | In the lab, 24.12.3 kept *Add Title Clip* disabled. The 25.04.3 title project (with `root` sanitized) was opened in 24.12.3 instead, which reported the title font as missing and offered a placeholder: accepted, so the font is stored as `Noto Sans`. Then saved. |
| `upstream/test-keyframes.resaved-<v>.3.kdenlive` | `test-keyframes.resaved-26.08.2.kdenlive` opened, saved. |

## Sanitization applied to Kdenlive-saved files

Kdenlive writes absolute machine paths into projects. The following values were rewritten
after saving, and nothing else was changed:

- **`root` attribute:** `<mlt root="…">` → `/nonexistent/kdenlive-mcp-fixture-root`. On load, Kdenlive treats a missing root as a moved project and resolves relative media against the project file's directory, which is exactly where the media is.
- **`browserurl` property:** `kdenlive:docproperties.browserurl` (the last directory opened in a file dialog) → the same placeholder.
- **`subtitlesList` property:** absolute paths in `kdenlive:sequenceproperties.subtitlesList` → the same placeholder. Kdenlive keeps the pre-*Save As* name there.

Privacy note: real projects saved by Kdenlive contain absolute local paths, including the
home directory. Do not commit real projects as fixtures.
