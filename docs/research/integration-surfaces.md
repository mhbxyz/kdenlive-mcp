# Kdenlive integration surfaces and project compatibility

Research report for [#2](https://github.com/mhbxyz/kdenlive-mcp/issues/2). Date: 2026-11-08.

This report records what was actually tested, what is documented upstream, and what remains
unknown. It is the evidence base for choosing backends in the following milestones.

## 1. Environment and method

| Item | Observed value |
|---|---|
| OS | Linux (CachyOS), KDE Plasma session on Wayland |
| Kdenlive | 26.08.2, distribution package, `/usr/bin/kdenlive`, plus `/usr/bin/kdenlive_render` |
| MLT / melt | 7.42.0 (`/usr/bin/melt`); `libmltmovit` and `libmltrtaudio` fail to load (missing system libraries) |
| FFmpeg / ffprobe | n9.0.2 |
| Python | 3.14.8 (system), 3.13 available through uv |
| Upstream source read | `kdenlive-26.08.2.tar.xz` from download.kde.org (read only, never built or executed) |

These are observations on one machine, not minimum versions.

Every experiment ran in a disposable lab directory:

- **Isolated configuration:** `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_CACHE_HOME` pointed into the lab, so the user's Kdenlive configuration, recent files and caches were never read or written.
- **Private D-Bus:** a private session bus from `dbus-run-session`, and a private AT-SPI bus (a dedicated `dbus-daemon` using `accessibility.conf`, plus `at-spi2-registryd`).
- **Headless display:** either Qt `offscreen`, or a headless compositor started with `kwin_wayland --virtual`.
- **Synthetic media:** generated with FFmpeg `lavfi` sources (`testsrc2`, `smptebars` at 30000/1001 fps, `sine`, `color`), including a file name with a space and a non-ASCII character.
- **Reference projects:** the upstream `tests/dataset/*.kdenlive` files, copied into the lab.

No real user project was opened or modified. The tools are in [`research/lab/`](../../research/lab/README.md).

## 2. Surfaces investigated

### 2.1 Project file (`.kdenlive`, MLT XML + Kdenlive metadata) — documented

Upstream `dev-docs/fileformat.md` (CC0) documents the current format.

- **Format generation:** generation 5, document version `1.1`, used since Kdenlive 23.04. Kdenlive 26.08.2 still writes `kdenlive:docproperties.version = 1.1`.
- **Project-level metadata:** the `main_bin` playlist holds project properties (`kdenlive:docproperties.*`) and one `<entry>` per bin clip, including sequence clips.
- **Timeline sequences:** each sequence is a `<tractor>` whose `id` is a `{uuid}` and that carries `kdenlive:uuid`, `kdenlive:producer_type=17` and `kdenlive:sequenceproperties.*`.
- **Tracks:** each timeline track is itself a `<tractor>` with two `<playlist>` children; the second one is used for same-track mixes. Audio tracks carry `kdenlive:audio_track=1` and `hide="video"`, video tracks `hide="audio"`. Internal filters (volume, panner, audiolevel) and internal transitions (`mix`, `qtblend`/`frei0r.cairoblend`) carry `internal_added=237`.
- **Playback wrapper:** the last `<tractor>` has `kdenlive:projectTractor=1` and only wraps the active sequence, so that `melt project.kdenlive` plays it.

**Observed on 26.08.2 output** (a project created and saved by Kdenlive itself through the GUI lab, §3):

- **Media type:** media clips are written as `<chain>` elements with `mlt_service=avformat-novalidate`. Images are `<producer>`. The black background is `producer0`, `mlt_service=color`.
- **Default timeline:** a new project gets 2 audio tracks and 2 video tracks (`kdenlive:sequenceproperties.tracksCount=4`).
- **Time representation:** `in`, `out` and blank lengths are written as **clock strings `HH:MM:SS.mmm`**, not frame numbers, while `length` is a frame count. Frame accuracy at rational frame rates therefore depends on converting through the profile's `frame_rate_num/den`. Millisecond strings are ambiguous at 30000/1001 fps unless they are rounded to the nearest frame.
- **Media paths:** `resource` holds an absolute path when the media is outside the project directory, and the root `<mlt root="...">` attribute holds the project directory. Upstream test files contain relative `resource` values.
- **Project ids:** bin clip ids (`kdenlive:id`), `kdenlive:control_uuid`, the document uuid and the sequence uuid are all regenerated identifiers that Kdenlive cross-references. An editor must keep them consistent.

**Path resolution, from the source** (`documentvalidator.cpp`, `documentchecker.cpp`, `kdenlivedoc.cpp`):

- **Relative resources:** they resolve against the `root` attribute.
- **Missing `root`:** Kdenlive uses the directory of the project file.
- **`root` that no longer exists:** Kdenlive treats the project as moved. It uses the project file's directory instead and rewrites absolute paths that started with the old root.
- **`$CURRENTPATH`:** a placeholder used by archived projects.

Consequence: resolving paths against the current working directory is always wrong.

**Upgrade behaviour:** opening an older document upgrades it in memory. When Kdenlive changes the file this way, it writes a `<name>_backup0.kdenlive` copy next to the project and shows an *Information* dialog ("Your project file was modified by Kdenlive… a backup copy… was created"). Saving an *unmodified* document is a no-op: the file keeps its old version.

**XML hygiene:** older (gen-2) files can use an undeclared `kdenlive:` namespace. Parsers must tolerate this, and must not resolve DTDs or external entities.

### 2.2 MLT / `melt` — documented

- **Rendering:** `melt project.kdenlive -consumer avformat:out.mp4 …` rendered a Kdenlive 26.08.2 project in about 1.2 s (5 s of 1080p25, H.264 and AAC), reading the project tractor.
- **Missing media:** melt renders projects with missing media without failing (exit 0, short or black output). Its exit status alone proves nothing.
- **Not an editing backend:** MLT ignores `kdenlive:` metadata, so **an MLT-renderable file is not evidence of Kdenlive editability**. A file written by `melt -consumer xml` has none of the `main_bin`/sequence structure Kdenlive expects.

### 2.3 Kdenlive command line — documented in `--help`

- **Command-line rendering:** `kdenlive --render [--render-preset NAME] project.kdenlive out.ext` rendered headlessly with `QT_QPA_PLATFORM=offscreen` in about 1.5 s. The default preset is MP4-H264/AAC. Preset names come from `/usr/share/kdenlive/export/profiles.xml` (and user custom presets in `XDG_DATA_HOME`).
- **Failure modes:**
  - **Missing media:** the render exits **0** and produces a short output, with no warning on stdout or stderr.
  - **Nonexistent project path:** the render **crashes with exit status 139 (SIGSEGV)**.
  - Rendering therefore needs its own pre-flight checks (project exists, media resolvable, output policy) and post-render output validation (ffprobe).
- **`--setup-report FILE`:** writes a small JSON with MLT's version but an **empty Kdenlive and FFmpeg version** on this installation. It is not reliable enough for diagnostics; `--version` works.
- **`-i clips`:** adds clips to the bin of a new project in the GUI. It is not useful headlessly.

### 2.4 D-Bus — undocumented, introspected

Kdenlive registers `org.kde.kdenlive-<pid>`. On `/kdenlive/MainWindow_1`, interface `org.kde.kdenlive.MainWindow` exposes a few public slots:

- **Bin and timeline:** `addProjectClip(url[, folder])` adds a bin clip. `addTimelineClip(url)` only inserts a clip **already in the bin** at the playhead (source: `MainWindow::addTimelineClip`).
- **Effects and subtitles:** `addEffect(effectId)`, `slotAddSubtitle(text)`.
- **Rendering:** `scriptRender(url)`, which ignores its argument and opens the render widget. `setRenderingProgress`/`setRenderingFinished` are callbacks used by the render process.
- **Application:** `exitApp()`.
- **Misleading name:** `updateProjectPath(path)` only resets the **render output path**; it does not set the document path.

`org.kde.KMainWindow.activateAction(name)` can trigger any of the **807 KActions** listed by `org.kde.KMainWindow.actions` (`file_save`, `cut_timeline_clip`, `effect_*`, `transition_*`, `add_marker_guide_quickly`, `insert_track`, …). In practice:

- **Some actions work:** `addProjectClip`, then `addTimelineClip` after the clip finished loading, inserted a clip.
- **Many actions silently do nothing:** `activateAction` returned `true` for seek, cut, select, guide and transition actions, but none changed the saved timeline. Actions act on the focused monitor, the active track and the selection, which cannot be observed or set reliably over D-Bus.
- **Modal dialogs block the caller:** `file_save` on an untitled document opens a Save dialog and blocks the D-Bus call until the dialog is answered.

Conclusion: **D-Bus is not an editing API.** It is useful for a few coarse operations on a running, visible instance. It gives no read access to the timeline state, no error reporting, and no stability guarantee.

### 2.5 GUI automation (AT-SPI) — experimental

With accessibility enabled (`QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`), the whole Qt widget tree of Kdenlive is readable and actionable through AT-SPI. That includes the splash screen, the KDE file dialog, missing-media and upgrade dialogs, menus and toolbar buttons. Upstream itself uses this through `selenium-webdriver-at-spi` (`appiumtests/`).

What the lab achieved:

- **Project creation:** with `addProjectClip` + `addTimelineClip`, then *Save* and the KDE file dialog filled through AT-SPI, Kdenlive 26.08.2 created and saved a new project with a timeline clip.
- **Upgrade-and-save:** an upstream 23.04 project was opened, upgraded and saved as 26.08.2.
- **Dialog detection:** loading dialogs were detected and listed: the missing-clips dialog with *Search Recursively / Use Placeholders / Remove / Abort*, and the upgrade information dialog.

Limits:

- **Blocking modals:** activating a control that opens a modal dialog never returns an AT-SPI reply. It has to be treated as fire-and-forget.
- **Fragile layout:** the QML timeline exposes little to accessibility, labels are localized (`LANG`/`LANGUAGE` must be forced), layout and dialogs change between versions, and a desktop session or a nested compositor is required.
- **Real HOME:** the real `HOME` is still used by file dialogs, which default to `~/Videos`.

Conclusion: AT-SPI is a **valuable test harness** (load, resave and dialog detection on real Kdenlive), not a product backend.

### 2.6 Other surfaces

- **OpenTimelineIO:** Kdenlive has *OpenTimelineIO Import/Export* menu actions (`otiointerface.py`). It is an interchange path worth evaluating later for import and export. It is not a lossless editing path.
- **Native plugins / scripting:** no plugin API or embedded scripting surface was found in 26.08.2. The `scripts/` directory holds helper scripts for speech-to-text, masking and GPU checks, not an automation API.

## 3. Capability and compatibility matrix

Legend: ✅ verified here · 🟡 partially verified / with caveats · ❌ not viable · ❔ unknown.

| Capability | Project XML (direct) | melt | `kdenlive --render` | D-Bus | AT-SPI GUI |
|---|---|---|---|---|---|
| Read project / timeline | ✅ structure documented and observed | ❌ no Kdenlive semantics | ❌ | ❌ no read API | 🟡 only what widgets show |
| Detect missing media | ✅ with Kdenlive path rules | ❌ silent | ❌ silent (exit 0) | ❌ | ✅ dialog visible |
| Create editable project | 🟡 feasible, must match gen-5 structure; verify by Kdenlive load + resave | ❌ not editable | ❌ | 🟡 clips only, needs a visible session | ✅ but fragile |
| Edit existing project | 🟡 feasible with id/uuid consistency and preservation of unknown data | ❌ | ❌ | ❌ context-dependent actions | 🟡 fragile |
| Render | ❌ | ✅ fast, no Kdenlive preset semantics | ✅ official presets; needs pre-flight + output validation | 🟡 opens the render dialog | 🟡 |
| Live state of an open project | ❌ disk only | ❌ | ❌ | 🟡 coarse actions, no state read | 🟡 |
| Version sensitivity | medium: format gen 5 since 23.04, internal ids/properties evolve | low | medium: preset names, CLI flags | high: undocumented | very high |

Projects open in Kdenlive while edited on disk (§2.1, §2.4):

- **No disk-change warning:** Kdenlive keeps the document in memory and keeps a lock/autosave in its cache. No reload-on-change behaviour was observed or found in the code paths read.
- **Clobbering:** saving in Kdenlive overwrites external changes. External writes to an open project are therefore unsafe by default. **Not directly demonstrated** with two writers in this lab; see the follow-up below.

## 4. Recommendations

1. **Read path (#4, #5):**
   - **Parser:** parse `.kdenlive` XML directly with DTD and external entities forbidden.
   - **Time and paths:** model time as integer frames with the profile's rational frame rate, and convert Kdenlive clock strings by rounding to the nearest frame. Implement the root / project-dir / moved-project path rules above.
   - **Media probing:** use ffprobe, with timeouts.
2. **Render (#6, #7):**
   - **Primary path:** `kdenlive --render` with an explicit preset, after pre-flight checks (input exists, media resolvable, output absent unless overwrite is requested, preset known). Never trust the exit status alone; validate the output with ffprobe.
   - **Fallback:** document `melt` as a fallback and diagnostic path, not as the default.
   - **Process control:** run every subprocess with argument lists, timeouts and process-group kill.
3. **Create and edit (#8–#11):**
   - **Writer:** write gen-5 XML modeled on Kdenlive 26.08.2 output, preserving unknown elements and properties, and refuse files the editor does not fully understand.
   - **Verification harness:** let real Kdenlive open the file headlessly (AT-SPI lab), assert that no error or missing-media dialog appears, save it, then re-parse and compare (clips, positions, ids), and render it.
   - **Manual checks:** keep GUI editing checks as explicit manual checklists.
4. **Live control (#20):** D-Bus actions and AT-SPI are the only live surfaces. Both are unstable and context-dependent, so treat live control as research, not as a supported feature.
5. **Test corpus (#19):** generate fixtures through the lab with synthetic media, and keep the generation scripts. Upstream test projects can be used for local experiments but are not redistributed here.

## 5. Open questions and follow-ups

- **Concurrent writers:** demonstrate Kdenlive overwriting an external edit to an open project (two-writer test) and check the lock-file semantics. → #9
- **Frame mapping:** confirm the clock-string rounding rule at 30000/1001 and 24000/1001 fps by round-tripping a project through Kdenlive. → #4, #19
- **Relative paths on save:** determine when Kdenlive writes relative versus absolute `resource` paths. → #5, #10
- **Title and subtitle formats:** check whether titles (`kdenlivetitle` producer) and subtitles (`avfilter.subtitles` + `<project>.kdenlive.srt`) round-trip when written externally. → #15, #16
- **Preset discovery:** confirm how presets are discovered on other packagings (Flatpak, AppImage) and whether `kdenlive_render` can be invoked directly as a stable interface (no evidence yet). → #6
- **Missing system libraries:** `libmltmovit` and `libmltrtaudio` are absent here. Report them in diagnostics; they do not block CPU rendering. → #3
