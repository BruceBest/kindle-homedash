#!/bin/sh
# Name: HomeDash Start
# Author: Hermes
# DontUseFBInk
# Starts the Home Assistant dashboard. Press the power button to exit.
if [ -f /tmp/homedash.pid ] && kill -0 "$(cat /tmp/homedash.pid)" 2>/dev/null; then
    exit 0
fi
# scriptlets are killed after 60 s, so detach the long-running loop
setsid sh /mnt/us/homedash/loop.sh >/dev/null 2>&1 < /dev/null &
exit 0
