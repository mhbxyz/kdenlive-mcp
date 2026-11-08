#!/bin/bash
# kdrender.sh LAB PROJECT OUT [preset] : Kdenlive CLI render in isolated XDG env + private bus.
L=$1; export XDG_CONFIG_HOME=$L/xdg/config XDG_DATA_HOME=$L/xdg/data XDG_CACHE_HOME=$L/xdg/cache QT_QPA_PLATFORM=offscreen
names='xdg-desktop-portal|ksecretd|kioworker|xdg-document-portal|xdg-permission-store'
before=$(pgrep -f "$names" | sort)
dbus-run-session -- kdenlive --render ${4:+--render-preset "$4"} "$2" "$3" > $L/render.log 2>&1 < /dev/null; rc=$?
after=$(pgrep -f "$names" | sort); new=$(comm -13 <(echo "$before") <(echo "$after")); [ -n "$new" ] && kill $new 2>/dev/null
echo "kdenlive rc=$rc"
