#!/bin/sh
# Name: HomeDash Test (one frame)
# Author: Hermes
# Fetches one dashboard frame and draws it over the current screen (UI keeps
# running). Tap anywhere / turn a page to redraw the UI afterwards.
. /mnt/us/homedash/env.sh
LOG=/mnt/us/homedash/log.txt
lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null
bat=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null)
if wget -q -T 20 -O /tmp/homedash_test.png "$URL?batteryLevel=$bat"; then
    /mnt/us/libkh/bin/fbink -q -c -f -g file=/tmp/homedash_test.png
    echo "$(date '+%F %T') test OK" >> "$LOG"
else
    echo "HomeDash: fetch failed from $URL"
    echo "$(date '+%F %T') test FAILED" >> "$LOG"
fi
