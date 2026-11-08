"""Isolated Kdenlive GUI lab for research only (not part of the package).

Runs Kdenlive under a headless ``kwin_wayland --virtual`` compositor with its own XDG
config/data/cache directories, a private session D-Bus (provided by ``kdgui.sh``) and a
private AT-SPI accessibility bus, then executes a small command script against it.

Usage (normally through kdgui.sh):  kdgui.py LAB_DIR SCRIPT_FILE
Kdenlive arguments come from the KD_ARGS environment variable (shell-split); KD_BIN selects
the Kdenlive executable (default: kdenlive on PATH).

Script commands, one per line:
  sleep N                      wait N seconds
  dialogs                      list top-level windows (role + title)
  dump [depth]                 dump the accessibility tree of every window
  ddump [depth]                dump only dialog/alert windows
  click NAME                   activate the first button/menu item named NAME (any window)
  dclick NAME                  activate a button named NAME inside a dialog only
  dsettext COMBO TEXT          set the text field inside the dialog combo box named COMBO
  dbus PATH METHOD [ARGS...]   call qdbus6 against the running Kdenlive service
  run PROGRAM [ARGS...]        run a program with the lab environment (private session bus)

Never point it at real user projects: HOME is still the real home directory and Kdenlive's
file dialogs default to it, so always type absolute paths inside a disposable lab directory.
"""
import os, shlex, subprocess, sys, time
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

lab, script = sys.argv[1], sys.argv[2]
base = dict(os.environ, XDG_CONFIG_HOME=f"{lab}/xdg/config", XDG_DATA_HOME=f"{lab}/xdg/data",
            XDG_CACHE_HOME=f"{lab}/xdg/cache", XDG_RUNTIME_DIR=f"{lab}/run")
os.makedirs(f"{lab}/run", mode=0o700, exist_ok=True)
procs = []
def spawn(cmd, env, name):
    f = open(f"{lab}/{name}.log", "w")
    p = subprocess.Popen(cmd, env=env, stdout=f, stderr=f, start_new_session=True)
    procs.append(p); return p
a11y = subprocess.Popen(["dbus-daemon", "--config-file=/usr/share/defaults/at-spi2/accessibility.conf", "--print-address", "--nofork"],
                        stdout=subprocess.PIPE, text=True, start_new_session=True, env=base)
procs.append(a11y)
base["AT_SPI_BUS_ADDRESS"] = a11y.stdout.readline().strip()
os.environ["AT_SPI_BUS_ADDRESS"] = base["AT_SPI_BUS_ADDRESS"]
spawn(["/usr/lib/at-spi2-registryd"], base, "registry")
time.sleep(1)
sock = f"kdlab-{os.getpid()}"
wenv = dict(base, XDG_RUNTIME_DIR=os.environ["XDG_RUNTIME_DIR"])
spawn(["kwin_wayland", "--virtual", "--no-lockscreen", "--socket", sock, "--width", "1600", "--height", "900"], wenv, "kwin")
time.sleep(3)
kenv = dict(wenv, WAYLAND_DISPLAY=sock, QT_QPA_PLATFORM="wayland", QT_LINUX_ACCESSIBILITY_ALWAYS_ON="1",
            QT_ACCESSIBILITY="1", QT_QPA_PLATFORMTHEME=os.environ.get("KD_THEME", "kde"), LANG="C.UTF-8", LANGUAGE="en", LC_ALL="C.UTF-8")
kenv.pop("DISPLAY", None)
# KD_BIN: another Kdenlive (e.g. an AppImage of an older release, #66); default: kdenlive on PATH.
# KD_FLAGS: startup options (default --no-welcome, which Kdenlive 24.x does not know).
kd = spawn([os.environ.get("KD_BIN", "kdenlive"), *shlex.split(os.environ.get("KD_FLAGS", "--no-welcome")),
            *shlex.split(os.environ.get("KD_ARGS", ""))], kenv, "kdenlive")

def app():
    global _init
    if not globals().get("_init"): Atspi.init(); _init = True
    d = Atspi.get_desktop(0)
    for i in range(d.get_child_count()):
        a = d.get_child_at_index(i)
        if a and "kdenlive" in (a.get_name() or "").lower(): return a
def walk(node, depth, maxd, out):
    try:
        role, name = node.get_role_name(), node.get_name()
        n = node.get_child_count()
    except Exception: return
    out.append(f"{'  '*depth}[{role}] {name!r}" + (f" ({n})" if n else ""))
    if depth < maxd:
        for i in range(min(n, 200)):
            c = node.get_child_at_index(i)
            if c: walk(c, depth + 1, maxd, out)
def find(node, pred, depth=0, maxd=25):
    try:
        if pred(node): return node
        n = node.get_child_count()
    except Exception: return None
    if depth >= maxd: return None
    for i in range(min(n, 300)):
        c = node.get_child_at_index(i)
        if c:
            r = find(c, pred, depth + 1, maxd)
            if r: return r
def windows():
    a = app()
    if not a: return []
    return [a.get_child_at_index(i) for i in range(a.get_child_count())]
def qd(*a, t=20):
    try:
        r = subprocess.run(["qdbus6", *a], capture_output=True, text=True, timeout=t, env=kenv)
        return (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired: return "TIMEOUT"
def svc():
    return next((l.strip() for l in qd().splitlines() if "kdenlive" in l), None)

for _ in range(int(os.environ.get("KD_WAIT", "60"))):  # AppImages start slower (#66)
    time.sleep(1)
    if app() and svc(): break
time.sleep(5)
print("app:", bool(app()), "svc:", svc(), flush=True)
try:
    for line in open(script):
        line = line.strip()
        if not line or line.startswith("#"): continue
        p = shlex.split(line); cmd = p[0]
        if cmd == "sleep": time.sleep(float(p[1]))
        elif cmd == "dialogs" or cmd == "title":
            for w in windows(): print("WINDOW", w.get_role_name(), repr(w.get_name()), flush=True)
        elif cmd == "dsettext":
            combo, text = p[1], p[2]; hit = None
            for w in windows():
                if w.get_role_name() not in ("dialog", "alert"): continue
                c = find(w, lambda n: n.get_role_name() == "combo box" and n.get_name() == combo)
                if c: hit = find(c, lambda n: n.get_role_name() == "text")
                if hit: break
            if hit:
                print("DSETTEXT", combo, hit.get_editable_text_iface().set_text_contents(text), flush=True)
            else: print("DSETTEXT not found", flush=True)
        elif cmd == "dclick":
            target = p[1]; hit = None
            for w in windows():
                if w.get_role_name() not in ("dialog", "alert"): continue
                hit = find(w, lambda n: n.get_name() == target and n.get_role_name() in ("push button", "button"))
                if hit: break
            if hit:
                try: res = hit.get_action_iface().do_action(0)
                except Exception as e: res = f"sent (no reply) {type(e).__name__}"
                print("DCLICK", target, "->", res, flush=True)
            else: print("DCLICK", target, "-> NOT FOUND", flush=True)
        elif cmd == "ddump":
            out = []
            for w in windows():
                if w.get_role_name() in ("dialog", "alert"): walk(w, 0, int(p[1]) if len(p) > 1 else 8, out)
            print("\n".join(l for l in out[:300] if not l.strip().startswith(("[filler] ''", "[panel] ''"))), flush=True)
        elif cmd == "dump":
            out = []
            for w in windows(): walk(w, 0, int(p[1]) if len(p) > 1 else 4, out)
            print("\n".join(out[:400]), flush=True)
        elif cmd == "click":
            target = p[1]; hit = None
            for w in windows():
                hit = find(w, lambda n: n.get_name() == target and n.get_role_name() in ("push button", "button", "menu item", "check box", "radio button", "toggle button"))
                if hit: break
            if hit:
                act = hit.get_action_iface()
                try: res = act.do_action(0) if act else "no action iface"
                except Exception as e: res = f"sent (no reply: modal?) {type(e).__name__}"
                print("CLICK", target, "->", res, flush=True)
            else: print("CLICK", target, "-> NOT FOUND", flush=True)
        elif cmd == "dbus":
            print("DBUS", line, "=>", qd(svc(), *p[1:])[:40000], flush=True)
        elif cmd == "run":
            r = subprocess.run(p[1:], capture_output=True, text=True, timeout=120, env=kenv)
            print("RUN", " ".join(p[1:]), "=>", r.returncode, r.stdout[-6000:], r.stderr[-2000:], flush=True)
        elif cmd == "key":
            for ch in p[1]:
                Atspi.generate_keyboard_event(0, ch, Atspi.KeySynthType.STRING)
            print("KEY sent", flush=True)
        elif cmd == "enter":
            Atspi.generate_keyboard_event(65293, None, Atspi.KeySynthType.PRESSRELEASE | Atspi.KeySynthType.SYM); print("ENTER sent", flush=True)
        elif cmd == "settext":
            target, text = p[1], p[2]; hit = None
            for w in windows():
                hit = find(w, lambda n: n.get_role_name() in ("text", "entry") and (n.get_name() == target or target == "*"))
                if hit: break
            if hit:
                et = hit.get_editable_text_iface(); print("SETTEXT", target, et.set_text_contents(text), flush=True)
            else: print("SETTEXT not found", flush=True)
finally:
    s = svc()
    if s: qd(s, "/kdenlive/MainWindow_1", "org.kde.kdenlive.MainWindow.exitApp", t=5)
    time.sleep(3)
    for pr in reversed(procs):
        if pr.poll() is None:
            try: os.killpg(pr.pid, 9)
            except ProcessLookupError: pass
    for suffix in ("", ".lock"):
        try: os.unlink(os.path.join(os.environ["XDG_RUNTIME_DIR"], sock + suffix))
        except FileNotFoundError: pass
    print("done", flush=True)
