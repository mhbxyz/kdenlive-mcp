#!/bin/bash
# kdgui.sh LAB SCRIPT OUT : run kdgui.py in a private D-Bus session and clean helper services afterwards.
names='xdg-desktop-portal|ksecretd|kioworker|kded6|xdg-document-portal|xdg-permission-store|at-spi|Xwayland|kwin_wayland'
before=$(pgrep -f "$names" | sort)
dbus-run-session -- python3 -I "$(dirname "$0")/kdgui.py" "$1" "$2" > "$3" 2>&1 < /dev/null
after=$(pgrep -f "$names" | sort)
new=$(comm -13 <(echo "$before") <(echo "$after"))
[ -n "$new" ] && kill -9 $new 2>/dev/null
exit 0
