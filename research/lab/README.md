# Research lab

Throwaway tools used for [docs/research/integration-surfaces.md](../../docs/research/integration-surfaces.md).
They are **not** part of the `mcp-kdenlive` package and are not supported interfaces.

| Tool | Purpose |
|---|---|
| `kdgui.sh` / `kdgui.py` | Run Kdenlive headlessly (`kwin_wayland --virtual`) with isolated XDG directories, a private session D-Bus and a private AT-SPI bus. Then run a small command script against it: list dialogs, click buttons, fill the save dialog, call D-Bus methods. |
| `kdrender.sh` | Run `kdenlive --render` offscreen with isolated XDG directories and a private D-Bus. |
| `kdsum.py` | Summarize a `.kdenlive` file: bin, sequences, tracks, playlist entries, transitions. |

Requirements: Linux, Kdenlive, `kwin_wayland`, `qdbus6`, `dbus-run-session`, `at-spi2-core`
(`at-spi2-registryd`) and Python GObject bindings for `Atspi` 2.0.

## Safety rules

- Use a disposable lab directory, for example `LAB=$(mktemp -d)`, containing `xdg/{config,data,cache}`. Never use a real project.
- `HOME` is still the real home directory. Kdenlive's file dialogs default to `~/Videos`, so always type absolute paths inside the lab.
- `kdgui.sh` and `kdrender.sh` kill the helper services (portals, ksecretd, …) that their private sessions spawned.

## Example: let Kdenlive create a project

```sh
LAB=$(mktemp -d); mkdir -p "$LAB"/xdg/{config,data,cache} "$LAB"/media
ffmpeg -f lavfi -i testsrc2=size=640x360:rate=25:duration=5 -f lavfi -i sine=duration=5 \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$LAB/media/a.mp4"
M=/kdenlive/MainWindow_1; I=org.kde.kdenlive.MainWindow
cat > "$LAB/script.txt" <<EOF
dbus $M $I.addProjectClip $LAB/media/a.mp4
sleep 4
dbus $M $I.addTimelineClip $LAB/media/a.mp4
sleep 3
click Save
sleep 4
dsettext Name: $LAB/new.kdenlive
dclick Save
sleep 4
dialogs
EOF
KD_THEME=kde ./kdgui.sh "$LAB" "$LAB/script.txt" "$LAB/out.txt"
python3 kdsum.py "$LAB/new.kdenlive"
```

## Example: load and re-save an existing project

```sh
printf 'click "Start Normally"\nsleep 4\ndialogs\nddump 5\nclick Save\nsleep 4\ndialogs\n' > "$LAB/resave.txt"
KD_ARGS="$LAB/copy.kdenlive" KD_THEME=kde ./kdgui.sh "$LAB" "$LAB/resave.txt" "$LAB/out.txt"
```

Kdenlive only writes the file if the document is marked modified. Opening an older document
upgrades it, writes `<name>_backup0.kdenlive` and shows an *Information* dialog.
