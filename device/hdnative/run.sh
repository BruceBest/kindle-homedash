#!/bin/sh
# 卧室 App (native): stops the Kindle UI, runs the hdnative touch client.
# Exit: tap "退出" on the home page, or press the power button.
DIR=/mnt/us/hdnative
LOG=$DIR/log.txt
# Per-device settings (not in git): $DIR/local.env may set SERVER / REMOTE
[ -f "$DIR/local.env" ] && . "$DIR/local.env"
SERVER=${SERVER:-http://dash-server.lan:8765}   # dashboard server on your LAN
REMOTE=${REMOTE:-}                              # same server over Tailscale (optional)
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

restore() {
    log "restoring UI"
    [ -n "$WATCH_PID" ] && kill "$WATCH_PID" 2>/dev/null
    [ -n "$APP_PID" ] && kill "$APP_PID" 2>/dev/null
    rm -f /tmp/hdnative.pid
    lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null
    # the stock UI needs ~10-20 s to come back; say so instead of leaving a frozen dashboard on screen
    FBINK=/mnt/us/libkh/bin/fbink
    [ -x "$FBINK" ] && "$FBINK" -q -c -m -y 12 "Exiting - Kindle home returns in about 20 seconds..." 2>/dev/null
    start lab126_gui 2>/dev/null || start framework 2>/dev/null
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

( lipc-wait-event com.lab126.powerd goingToScreenSaver >/dev/null 2>&1
  log "power button"; kill -TERM $$ ) &
WATCH_PID=$!

cp "$DIR/hdnative" /tmp/hdnative && chmod +x /tmp/hdnative
/tmp/hdnative -server "$SERVER" -remote "$REMOTE" -fbink /mnt/us/libkh/bin/fbink >> "$LOG" 2>&1 &
APP_PID=$!
wait $APP_PID
log "app exited rc=$?"
restore
