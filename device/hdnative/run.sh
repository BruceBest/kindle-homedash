#!/bin/sh
# 卧室 App (native): stops the Kindle UI, runs the hdnative touch client.
# Exit: tap "退出" on the home page. Power button: toggles screen off (blank) / on.
DIR=/mnt/us/hdnative
LOG=$DIR/log.txt
# Per-device settings (not in git): $DIR/local.env may set SERVER / REMOTE
[ -f "$DIR/local.env" ] && . "$DIR/local.env"
SERVER=${SERVER:-http://dash-server.lan:8765}   # dashboard server on your LAN
REMOTE=${REMOTE:-}                              # same server over Tailscale (optional)
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

restore() {
    log "restoring UI"
    [ -n "$WATCH_PID" ] && kill $WATCH_PID 2>/dev/null
    pkill -f "head -c 64 /dev/input" 2>/dev/null
    rm -f /tmp/hd_sleep /tmp/hd_app.pid
    [ -n "$APP_PID" ] && kill "$APP_PID" 2>/dev/null
    rm -f /tmp/hdnative.pid
    lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null
    # the stock UI needs ~10-20 s to come back; say so instead of leaving a frozen dashboard on screen
    FBINK=/mnt/us/libkh/bin/fbink
    [ -x "$FBINK" ] && "$FBINK" -q -c -m -y 12 "Exiting - Kindle home returns in about 20 seconds..." 2>/dev/null
    start lab126_gui 2>/dev/null || start framework 2>/dev/null
    # after the stock UI is up, ask it to open Home so the e-ink repaints without a tap
    ( sleep 15; lipc-set-prop com.lab126.appmgrd start app://com.lab126.booklet.home >/dev/null 2>&1 ) &
    exit 0
}
trap restore INT TERM

# stop the old image-only dashboard if it is running
[ -f /tmp/homedash.pid ] && kill "$(cat /tmp/homedash.pid)" 2>/dev/null && sleep 3
echo $$ > /tmp/hdnative.pid
log "start server=$SERVER"

lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null
lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null
if [ -f /mnt/us/tailscale/start.sh ] && ! pidof tailscaled >/dev/null 2>&1; then
    setsid sh /mnt/us/tailscale/start.sh >/dev/null 2>&1 < /dev/null &
fi
# the framework would also react to taps -> stop it while the app owns the screen
stop lab126_gui 2>/dev/null || stop framework 2>/dev/null
sleep 4

# Power button: toggles "screen off" (blank white screen, app paused) / back on.
# The stock framework is stopped, so powerd never reports the press; read the key device directly.
FBINK=/mnt/us/libkh/bin/fbink
PWRDEV=""
for d in /dev/input/event*; do
    n=$(basename "$d")
    if grep -q "bd71827-power\|snvs-powerkey" "/sys/class/input/$n/device/name" 2>/dev/null; then PWRDEV="$PWRDEV $d"; fi
done
rm -f /tmp/hd_sleep /tmp/hd_app.pid
powerwatch() {   # $1 = key device; one press+release = 64 bytes (4 events)
    while :; do
        head -c 64 "$1" >/dev/null 2>&1 || { sleep 2; continue; }
        if [ -f /tmp/hd_sleep ]; then
            rm -f /tmp/hd_sleep; log "power: screen on"
        else
            touch /tmp/hd_sleep; log "power: screen off"
            [ -f /tmp/hd_app.pid ] && kill "$(cat /tmp/hd_app.pid)" 2>/dev/null
            sleep 1; [ -x "$FBINK" ] && "$FBINK" -q -c 2>/dev/null
        fi
        sleep 1
    done
}
WATCH_PIDS=""
for d in $PWRDEV; do powerwatch "$d" & WATCH_PIDS="$WATCH_PIDS $!"; done
[ -n "$WATCH_PIDS" ] && WATCH_PID="$WATCH_PIDS"
log "power key devices:${PWRDEV:- none}"

cp "$DIR/hdnative" /tmp/hdnative && chmod +x /tmp/hdnative
while :; do
    if [ -f /tmp/hd_sleep ]; then sleep 1; continue; fi      # screen off: app stays stopped
    /tmp/hdnative -server "$SERVER" -remote "$REMOTE" -fbink "$FBINK" >> "$LOG" 2>&1 &
    APP_PID=$!
    echo "$APP_PID" > /tmp/hd_app.pid
    wait $APP_PID
    rc=$?
    [ -f /tmp/hd_sleep ] && continue                          # killed for screen-off
    log "app exited rc=$rc"
    break
done
restore
