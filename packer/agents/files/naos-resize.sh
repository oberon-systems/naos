#!/bin/sh
# Reads one {"cols":N,"rows":M} per line from the control port and resizes
# ttyS0, which is the tty tmux sizes its session to. The port gives EOF while
# the runner holds no connection, so the read is reopened rather than trusted.

port=""
while [ -z "$port" ]; do
    for name in /sys/class/virtio-ports/*/name; do
        [ "$(cat "$name" 2>/dev/null)" = naos.ctl ] || continue
        port=${name%/name}
        port=/dev/${port##*/}
        break
    done
    [ -n "$port" ] || sleep 5
done

while true; do
    while IFS= read -r line; do
        cols=$(printf '%s' "$line" | sed -n 's/.*"cols" *: *\([0-9]\{1,\}\).*/\1/p')
        rows=$(printf '%s' "$line" | sed -n 's/.*"rows" *: *\([0-9]\{1,\}\).*/\1/p')
        if [ -z "$cols" ] || [ -z "$rows" ]; then
            continue
        fi
        stty -F /dev/ttyS0 rows "$rows" cols "$cols" 2>/dev/null || true
    done <"$port"
    sleep 1
done
