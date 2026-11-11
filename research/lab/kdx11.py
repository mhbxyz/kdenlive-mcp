"""X11 GUI lab: an isolated Kdenlive on a private Xvfb display, driven by keyboard and mouse.

Research only (not part of the package). Unlike kdgui.py (AT-SPI on kwin_wayland), it needs no
accessibility support, so it also drives the official AppImages that register neither on the
accessibility bus nor on D-Bus (#66, #86). Each Kdenlive gets its own XDG directories, TMPDIR and
session bus (`dbus-run-session`); nothing reaches the user's desktop or Kdenlive settings.
Screenshots are the evidence: look at them before trusting a step.

Requirements: Xvfb (KDX_XVFB, default on PATH), python-xlib and Pillow
(e.g. `uv run --with python-xlib --with pillow python research/lab/kdx11.py ...`).

  xvfb                       start the display (KDX_DISPLAY, default :78, 1920x1080)
  start NAME KDENLIVE [ARGS] start Kdenlive (`kdenlive`, a path, or an extracted AppImage dir)
  stop NAME                  kill that Kdenlive and its private session
  wins                       list top-level windows (id, class, title, geometry)
  max [WIN]                  resize the main Kdenlive window (or WIN) to the screen
  focus WIN                  raise and focus a window (hex id or class/title substring)
  shot FILE [X Y W H]        screenshot as PNG
  key K [K...]               key combos: ctrl+shift+s Return g ...
  type TEXT                  type text (any Unicode character, through a spare keycode)
  click X Y [N] [BUTTON]     click N times
  drag X1 Y1 X2 Y2           drag with the left button

Extracted AppImages (`--appimage-extract`) are started like their AppRun, with SDL's dummy audio
driver: their hook forces PulseAudio, and without a sound server Kdenlive 24.12/25.04 cannot open
their monitors ("Could not create the video preview window") and quit.
Runs live in KDX_LAB (default ~/.cache/kdenlive-mcp-lab). Use disposable copies of projects only.
"""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

LAB = Path(os.environ.get("KDX_LAB", Path.home() / ".cache/kdenlive-mcp-lab"))
DISPLAY = os.environ.get("KDX_DISPLAY", ":78")
W, H = 1920, 1080
os.environ["DISPLAY"] = DISPLAY

APPIMAGE_RUN = """set -e
this_dir="$1"; shift  # the name AppRun uses: the hooks read it
source "$this_dir"/apprun-hooks/craft-runenv-hook.sh
source "$this_dir"/apprun-hooks/linuxdeploy-plugin-qt-hook.sh
export SDL_AUDIODRIVER=dummy
exec "$this_dir"/AppRun.wrapped "$@"
"""


def _x():
    from Xlib import X, XK, display
    from Xlib.ext import xtest

    return X, XK, display, xtest


def xvfb():
    if subprocess.run(["pgrep", "-f", f"Xvfb {DISPLAY}"], capture_output=True).returncode == 0:
        return print("running")
    exe = os.environ.get("KDX_XVFB") or shutil.which("Xvfb") or sys.exit("Xvfb not found")
    subprocess.Popen(
        [exe, DISPLAY, "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(1.5)
    print("started")


def _command(kdenlive):
    path = Path(kdenlive)
    if (path / "AppRun.wrapped").exists():
        return ["bash", "-c", APPIMAGE_RUN, "kdx11", str(path)]
    if (path / "squashfs-root" / "AppRun.wrapped").exists():
        return ["bash", "-c", APPIMAGE_RUN, "kdx11", str(path / "squashfs-root")]
    return [kdenlive]


def _unmount(run):
    # the private session starts a document portal, which mounts a FUSE file system in TMPDIR
    for mount in Path("/proc/self/mounts").read_text().splitlines():
        point = mount.split()[1]
        if point.startswith(str(run)):
            subprocess.run(["fusermount3", "-uz", point], stderr=subprocess.DEVNULL)


def start(name, kdenlive, *args):
    run = LAB / "runs" / name
    if len(str(run / "tmp")) > 70:  # KIO and D-Bus sockets live there (108-byte limit)
        sys.exit(f"{run} is too long for socket paths: set a shorter KDX_LAB")
    if run.exists():
        _unmount(run)
        shutil.rmtree(run)
    for d in ("config", "data", "cache", "tmp"):
        (run / d).mkdir(parents=True)
    os.chmod(run / "tmp", 0o700)
    if (LAB / "kdenliverc").exists():  # e.g. first-run wizard already done
        shutil.copy(LAB / "kdenliverc", run / "config" / "kdenliverc")
    env = {
        "HOME": str(Path.home()),
        "USER": os.environ.get("USER", ""),
        "PATH": "/usr/bin:/bin",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "LANGUAGE": "en",
        "DISPLAY": DISPLAY,
        "QT_QPA_PLATFORM": "xcb",
        "SDL_AUDIODRIVER": "dummy",
        "XDG_CONFIG_HOME": str(run / "config"),
        "XDG_DATA_HOME": str(run / "data"),
        "XDG_CACHE_HOME": str(run / "cache"),
        "XDG_RUNTIME_DIR": str(run / "tmp"),
        "TMPDIR": str(run / "tmp"),
    }
    log = open(run / "kdenlive.log", "w")
    p = subprocess.Popen(
        ["dbus-run-session", "--", *_command(kdenlive), *args],
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
        cwd=run,
    )
    (run / "pid").write_text(str(p.pid))
    print(p.pid)


def stop(name):
    run = LAB / "runs" / name
    try:
        os.killpg(int((run / "pid").read_text()), signal.SIGKILL)
    except (ProcessLookupError, FileNotFoundError):
        pass
    time.sleep(0.5)
    _unmount(run)
    print("stopped")


def _windows():
    X, _, display, _ = _x()
    d = display.Display()
    out = []
    for c in d.screen().root.query_tree().children:
        try:
            if c.get_attributes().map_state == X.IsViewable:
                out.append((c, c.get_wm_class(), c.get_wm_name(), c.get_geometry()))
        except Exception:
            continue
    return d, out


def _pick(spec=None):
    d, ws = _windows()
    if spec and spec.startswith("0x"):
        return d, next(w for w in ws if w[0].id == int(spec, 16))
    found = [
        w
        for w in ws
        if (spec is None and w[1] and "kdenlive" in " ".join(w[1]).lower())
        or (spec and spec.lower() in f"{w[1]} {w[2]}".lower())
    ]
    return d, max(found, key=lambda w: w[3].width * w[3].height)


def wins():
    for c, cls, name, g in _windows()[1]:
        print(hex(c.id), cls, repr(name), g.x, g.y, g.width, g.height)


def max_(spec=None):
    d, (c, *_) = _pick(spec)
    c.configure(x=0, y=0, width=W, height=H)
    d.sync()


def focus(spec):
    X = _x()[0]
    d, (c, *_) = _pick(spec)
    c.raise_window()
    c.set_input_focus(X.RevertToParent, X.CurrentTime)
    d.sync()


def shot(path, *box):
    from PIL import Image

    X, _, display, _ = _x()
    bx, by, bw, bh = map(int, box) if box else (0, 0, W, H)
    raw = display.Display().screen().root.get_image(bx, by, bw, bh, X.ZPixmap, 0xFFFFFFFF)
    Image.frombytes("RGB", (bw, bh), raw.data, "raw", "BGRX").save(path)
    print(path)


def _tap(d, code, mods=()):
    X, _, _, xtest = _x()
    for m in mods:
        xtest.fake_input(d, X.KeyPress, m)
    xtest.fake_input(d, X.KeyPress, code)
    xtest.fake_input(d, X.KeyRelease, code)
    for m in reversed(mods):
        xtest.fake_input(d, X.KeyRelease, m)
    d.sync()


def key(*combos):
    _, XK, display, _ = _x()
    d = display.Display()
    names = {"ctrl": "Control_L", "shift": "Shift_L", "alt": "Alt_L"}
    for combo in combos:
        *mods, k = combo.split("+")
        codes = [d.keysym_to_keycode(XK.string_to_keysym(names[m])) for m in mods]
        sym = XK.string_to_keysym(k) or (ord(k) if len(k) == 1 else 0)
        _tap(d, d.keysym_to_keycode(sym) or sys.exit(f"no key {k}"), codes)
        time.sleep(0.08)


def type_(text):
    _, XK, display, _ = _x()
    d = display.Display()
    shift = d.keysym_to_keycode(XK.string_to_keysym("Shift_L"))
    for ch in text:
        sym = {" ": 0x20, "\n": XK.string_to_keysym("Return")}.get(ch)
        if sym is None:
            sym = ord(ch) if ord(ch) < 0x100 else 0x01000000 + ord(ch)
        code = d.keysym_to_keycode(sym)
        if code:
            _tap(d, code, [shift] if d.keycode_to_keysym(code, 0) != sym else [])
        else:  # no key for it on this keymap: map it to a spare keycode for one keystroke
            d.change_keyboard_mapping(255, [(sym, sym)])
            d.sync()
            time.sleep(0.05)
            _tap(d, 255)
        time.sleep(0.02)


def click(px, py, n=1, button=1):
    X, _, display, xtest = _x()
    d = display.Display()
    xtest.fake_input(d, X.MotionNotify, x=int(px), y=int(py))
    d.sync()
    time.sleep(0.05)
    for _ in range(int(n)):
        xtest.fake_input(d, X.ButtonPress, int(button))
        xtest.fake_input(d, X.ButtonRelease, int(button))
        d.sync()
        time.sleep(0.05)


def drag(x1, y1, x2, y2):
    X, _, display, xtest = _x()
    d = display.Display()
    x1, y1, x2, y2 = map(int, (x1, y1, x2, y2))
    xtest.fake_input(d, X.MotionNotify, x=x1, y=y1)
    xtest.fake_input(d, X.ButtonPress, 1)
    d.sync()
    for i in range(1, 21):
        time.sleep(0.03)
        xtest.fake_input(d, X.MotionNotify, x=x1 + (x2 - x1) * i // 20, y=y1 + (y2 - y1) * i // 20)
        d.sync()
    time.sleep(0.1)
    xtest.fake_input(d, X.ButtonRelease, 1)
    d.sync()


if __name__ == "__main__":
    cmd, *a = sys.argv[1:]
    {
        "xvfb": xvfb,
        "start": start,
        "stop": stop,
        "wins": wins,
        "max": max_,
        "focus": focus,
        "shot": shot,
        "key": key,
        "type": type_,
        "click": click,
        "drag": drag,
    }[cmd](*a)
