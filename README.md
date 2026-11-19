# kindle-homedash

Bedroom e-ink dashboard + remote for Home Assistant and Hermes, running on a jailbroken Kindle Paperwhite 4 (fw 5.18.1.1.1, SpiderCat).

## Layout
- `server/` - renderer service (Python/Pillow, Docker, host net :8765). Endpoints: `/` e-ink PNG, `/stamp`, `/health`, `/remote`, `/app/*` (browser multi-page), `/n/page|tap|stamp` (native app pages + tap zones). `~/kindle-dash-server` symlinks here.
- `client/` - native Kindle client (Go, linux/arm v7). Draws PNGs with fbink, reads goodix-ts touch from evdev, LAN-first with Tailscale proxy fallback. `~/kindle-native/client` symlinks here.
- `device/` - files copied to `/mnt/us` on the Kindle: `homedash/` (panel loop), `hdnative/run.sh` (native app launcher), `tailscale/` (userspace start/stop), `documents/` (Scriptlets shown as books).
- `host/` - OptiPlex helpers: USB deploy script, udisks polkit rule, OTA filler script.
- `bin/kindle-msg` - post to the message board.
- `hermes-plugin/kindle-status/` - Hermes observer plugin writing live session / approval state for the dash.
- `ha/` - exported Home Assistant climate automations (thermostat, hot cut-off, humidifier) and the helpers they need.
- `zigbee/` - mosquitto + Zigbee2MQTT compose (SONOFF Dongle Plus V2 / EFR32MG21, ember adapter). Live data dir stays in `~/zigbee`.

## Features
- **Native Kindle UI** is landscape (sidebar tabs). Set `LANDSCAPE_PORT=right|left` (server env) to match which side the Kindle's USB port is on.
- **Bedroom**: live temperature/humidity (C), target temperature (16-26 C, 0.5 steps) and target humidity (30-70 %, 5 steps) steppers, auto-thermostat / auto-humidify switches.
- **Modes page**: one tap sets both targets and the auto switches (beach / day / sleep / away / all off). The same controls exist in the browser app (`/app/room`, `/app/modes`; phone-friendly).
- Errors are shown as a banner; successful actions are silent.
- Access is limited to LAN / Tailscale / localhost (no public exposure, no login).

## Build / run
```
cd server && docker compose up -d --build
cd client && GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o hdnative-arm
```

## Configure
Replace `dash-server.lan` with your server's address:
- `device/homedash/env.sh` (`URL`) and `device/documents/homedash_remote.sh`
- native app: copy `device/hdnative/local.env.example` to `/mnt/us/hdnative/local.env` and set `SERVER` (LAN) and optional `REMOTE` (Tailscale)
- or pass `-server` / `-remote` to the client directly

## License
MIT

## Secrets (never committed)
HA token is mounted read-only from `~/.config/hass/token`; Tailscale auth keys are one-shot and deleted after use; Zigbee network key lives only in `~/zigbee/z2m/data`.
