#!/bin/sh
# Name: 卧室 App
# Author: Hermes
# DontUseFBInk
# Native touch app for the bedroom panel. Exit: tap 退出 or press power.
if [ -f /tmp/hdnative.pid ] && kill -0 "$(cat /tmp/hdnative.pid)" 2>/dev/null; then
    exit 0
fi
setsid sh /mnt/us/hdnative/run.sh >/dev/null 2>&1 < /dev/null &
exit 0
