#!/bin/sh
# Name: Tailscale 启动
# Author: Hermes
# DontUseFBInk
setsid sh /mnt/us/tailscale/start.sh >/dev/null 2>&1 < /dev/null &
exit 0
