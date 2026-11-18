#!/bin/sh
/mnt/us/tailscale/tailscale --socket=/tmp/tailscaled.sock down 2>/dev/null
killall tailscaled 2>/dev/null
echo "$(date '+%m-%d %H:%M:%S') stopped" >> /mnt/us/tailscale/log.txt
