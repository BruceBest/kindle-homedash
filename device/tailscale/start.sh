#!/bin/sh
# Start Tailscale on the Kindle (userspace mode + SOCKS/HTTP proxy on localhost:1055 + Tailscale SSH)
D=/mnt/us/tailscale
SOCK=/tmp/tailscaled.sock
LOG=$D/log.txt
log(){ echo "$(date '+%m-%d %H:%M:%S') $*" >> $LOG; }
lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null
if ! pidof tailscaled >/dev/null 2>&1; then
  mkdir -p $D/state
  log "starting tailscaled"
  setsid $D/tailscaled --tun=userspace-networking --statedir=$D/state --socket=$SOCK \
      --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1056 \
      >> $D/tailscaled.log 2>&1 < /dev/null &
  sleep 6
fi
if [ -f $D/authkey ]; then
  $D/tailscale --socket=$SOCK up --authkey=file:$D/authkey --hostname=kindle-pw4 --ssh --accept-dns=false >> $LOG 2>&1
else
  $D/tailscale --socket=$SOCK up --hostname=kindle-pw4 --ssh --accept-dns=false >> $LOG 2>&1
fi
if $D/tailscale --socket=$SOCK status >/dev/null 2>&1; then
  log "up: $($D/tailscale --socket=$SOCK ip -4 2>/dev/null)"
  rm -f $D/authkey   # one-time key, no longer needed once logged in
else
  log "up failed"
fi
