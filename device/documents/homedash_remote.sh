#!/bin/sh
# Name: 卧室面板 App
# Author: Hermes
# DontUseFBInk
# Opens the bedroom remote page (humidifier / heater) in the Kindle browser.
URL="http://dash-server.lan:8765/app"   # edit to your dashboard server
# make sure the dashboard loop is not holding the screen
if [ -f /tmp/homedash.pid ]; then
    kill "$(cat /tmp/homedash.pid)" 2>/dev/null
    sleep 2
fi
lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null
lipc-set-prop com.lab126.appmgrd start "app://com.lab126.browser?view=$URL" 2>/dev/null \
  || lipc-set-prop com.lab126.appmgrd start "app://com.lab126.browser" 2>/dev/null
exit 0
