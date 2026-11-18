#!/bin/sh
# HomeDash main loop (runs as root, detached from the scriptlet).
# Stops the Kindle UI, shows the dashboard PNG from the OptiPlex, refreshes
# every $INTERVAL s. Pressing the power button exits and restores the UI.
DIR=/mnt/us/homedash
. "$DIR/env.sh"
LOG="$DIR/log.txt"
IMG=/tmp/homedash.png
FBINK=/mnt/us/libkh/bin/fbink

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

restore() {
    log "restoring UI"
    [ -n "$WATCH_PID" ] && kill "$WATCH_PID" 2>/dev/null
    rm -f /tmp/homedash.pid
    lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null
    start lab126_gui 2>/dev/null || start framework 2>/dev/null
    exit 0
}
trap restore INT TERM

echo $$ > /tmp/homedash.pid
log "start url=$URL interval=${INTERVAL}s"

lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null
lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null
# bring Tailscale up alongside the dashboard (remote SSH from the OptiPlex)
if [ -f /mnt/us/tailscale/start.sh ] && ! pidof tailscaled >/dev/null 2>&1; then
    setsid sh /mnt/us/tailscale/start.sh >/dev/null 2>&1 < /dev/null &
fi
if [ "$STOP_UI" = 1 ]; then
    stop lab126_gui 2>/dev/null || stop framework 2>/dev/null
    log "ui stopped (rc=$?)"
    sleep 4
fi

# Power button -> leave dashboard, back to normal Kindle.
( lipc-wait-event com.lab126.powerd goingToScreenSaver >/dev/null 2>&1
  log "power button"; kill -TERM $$ ) &
WATCH_PID=$!

show() {  # $1 = full|partial
    if [ "$1" = full ]; then "$FBINK" -q -c -f -g file="$IMG"
    else "$FBINK" -q -g file="$IMG"; fi
}

BASE="${URL%/}"
POLL=${POLL:-30}

refresh() {
    bat=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null)
    chg=$(lipc-get-prop com.lab126.powerd isCharging 2>/dev/null)
    [ "$chg" = 1 ] && chg=true || chg=false
    if wget -q -T 20 -O "$IMG.tmp" "$BASE/?batteryLevel=$bat&isCharging=$chg" && [ -s "$IMG.tmp" ]; then
        mv "$IMG.tmp" "$IMG"
        n=$((n + 1))
        if [ $((n % FULL_EVERY)) -eq 1 ]; then show full; else show partial; fi
        last=$(date +%s)
    else
        log "fetch failed (bat=$bat)"
        lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null
    fi
}

# Every $POLL s ask for a tiny change token (/stamp): redraw at once when a
# Hermes message is posted or the humidifier flips, else every $INTERVAL s.
n=0; last=0; prev=""
while true; do
    cur=$(wget -q -T 10 -O - "$BASE/stamp" 2>/dev/null)
    now=$(date +%s)
    if [ -n "$cur" ] && [ "$cur" != "$prev" ]; then
        [ -n "$prev" ] && log "change detected"
        prev="$cur"; refresh
    elif [ $((now - last)) -ge "$INTERVAL" ]; then
        refresh
    fi
    sleep "$POLL"
done
