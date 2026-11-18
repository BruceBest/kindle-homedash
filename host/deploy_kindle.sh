#!/bin/bash
# Deploy staged HomeDash + Tailscale files to a USB-connected jailbroken Kindle, then unmount.
set -e
DEV=$(lsblk -nro NAME,LABEL | awk '$2=="Kindle"{print "/dev/"$1; exit}')
[ -n "$DEV" ] || { echo "no Kindle block device"; exit 2; }
M=$(findmnt -n -o TARGET "$DEV" || true)
if [ -z "$M" ]; then udisksctl mount -b "$DEV" >/dev/null; M=$(findmnt -n -o TARGET "$DEV"); fi
echo "mounted $DEV at $M"
J=~/kindle-jb
V=$(ls -d $J/ts-dl/tailscale_*_arm | tail -1)
mkdir -p "$M/tailscale" "$M/homedash"
cp $J/homedash/homedash/loop.sh $J/homedash/homedash/env.sh "$M/homedash/"
cp $J/homedash/documents/*.sh "$M/documents/"
cp $J/tspkg/documents/*.sh "$M/documents/"
cp $J/tspkg/tailscale/start.sh $J/tspkg/tailscale/stop.sh "$M/tailscale/"
cp "$V/tailscale" "$V/tailscaled" "$M/tailscale/"
# native touch app
mkdir -p "$M/hdnative"
cp ~/kindle-native/pkg/hdnative/run.sh ~/kindle-native/pkg/hdnative/local.env ~/kindle-native/pkg/hdnative/hdnative "$M/hdnative/"
cp ~/kindle-native/pkg/documents/*.sh "$M/documents/"
if [ -f ~/.config/kindle-ts/authkey ] && [ ! -d "$M/tailscale/state/profiles" ]; then
  cp ~/.config/kindle-ts/authkey "$M/tailscale/authkey"
fi
sync
ls -la "$M/tailscale" | sed -E 's/tskey-[A-Za-z0-9_-]+/<redacted>/g'
ls "$M/documents" | grep -E 'homedash|tailscale'
df -h "$M" | tail -1
udisksctl unmount -b "$DEV"
