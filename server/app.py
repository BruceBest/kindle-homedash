"""Kindle e-ink dashboard renderer.

Pulls states from Home Assistant REST API and renders a grayscale PNG sized
for a Kindle Paperwhite 4 (1072x1448, portrait). Rendered on every request.

Endpoints:
  GET /            -> PNG (query: batteryLevel=0-100, isCharging=true/false)
  GET /health      -> "ok"
"""
import io
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

import remote
import pages
import nativeui
import sys

HA_URL = os.environ.get("HA_URL", "http://127.0.0.1:8123").rstrip("/")
HA_TOKEN = open(os.environ.get("HA_TOKEN_FILE", "/run/secrets/ha_token")).read().strip()
TZ = ZoneInfo(os.environ.get("TZ_NAME", "America/Detroit"))
W, H = int(os.environ.get("WIDTH", 1072)), int(os.environ.get("HEIGHT", 1448))
PORT = int(os.environ.get("PORT", 8765))
WEATHER = os.environ.get("WEATHER_ENTITY", "weather.wo_de_jia")
HUMIDIFIER = os.environ.get("HUMIDIFIER_ENTITY", "switch.wo_shi_jia_shi_qi_outlet")
HEATER = os.environ.get("HEATER_ENTITY", "switch.wo_shi_qu_nuan_qi")
# Optional; empty until the Zigbee sensor is paired.
ROOM_TEMP = os.environ.get("ROOM_TEMP_ENTITY", "")
ROOM_HUM = os.environ.get("ROOM_HUM_ENTITY", "")
FONT = os.environ.get("FONT", "/fonts/NotoSansCJK-Regular.ttc")
FONT_BOLD = os.environ.get("FONT_BOLD", "/fonts/NotoSansCJK-Bold.ttc")
FONT_INDEX = 2  # SC face inside the .ttc collection

COND_ZH = {
    "clear-night": "晴", "cloudy": "阴", "exceptional": "异常", "fog": "雾",
    "hail": "冰雹", "lightning": "雷电", "lightning-rainy": "雷阵雨",
    "partlycloudy": "多云", "pouring": "暴雨", "rainy": "雨", "snowy": "雪",
    "snowy-rainy": "雨夹雪", "sunny": "晴", "windy": "大风",
    "windy-variant": "大风多云",
}
WEEKDAY_ZH = "一二三四五六日"


def font(size, bold=False):
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT, size, index=FONT_INDEX)
    except OSError:
        return ImageFont.truetype(FONT_BOLD if bold else FONT, size)


def ha(path, data=None):
    req = urllib.request.Request(
        HA_URL + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def state(entity):
    if not entity:
        return None
    try:
        return ha(f"/api/states/{entity}")
    except Exception:
        return None


def forecast():
    try:
        r = ha("/api/services/weather/get_forecasts?return_response",
               {"entity_id": WEATHER, "type": "daily"})
        return r["service_response"][WEATHER]["forecast"]
    except Exception:
        return []


def f2c(f):
    return (float(f) - 32) * 5 / 9


HERMES_DIR = os.environ.get("HERMES_DIR", "/hermes")
QUOTA_JSONL = os.environ.get("QUOTA_JSONL", "/quota/plus-quota-watch-api.jsonl")
PLATFORM_ZH = {"discord": "Discord", "weixin": "微信", "whatsapp": "WhatsApp",
               "email": "邮件", "api_server": "API", "telegram": "Telegram"}


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def latest_quota():
    try:
        with open(QUOTA_JSONL, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8192))
            last = f.read().decode(errors="ignore").strip().splitlines()[-1]
        return json.loads(last)
    except Exception:
        return None


def gateway():
    g = read_json(f"{HERMES_DIR}/gateway_state.json") or {}
    plats = g.get("platforms", {})
    up = [k for k, v in plats.items() if v.get("state") == "connected"]
    down = [k for k, v in plats.items() if v.get("state") != "connected"]
    alive = False
    try:
        os.kill(int(g.get("pid", 0)), 0)  # works only if pid ns shared; best effort
        alive = True
    except Exception:
        hb = f"{HERMES_DIR}/state/gateway.heartbeat"
        try:
            alive = (datetime.now().timestamp() - os.path.getmtime(hb)) < 600
        except Exception:
            alive = bool(up)
    return alive, up, down, g.get("active_agents", 0)


def next_jobs(n=3):
    d = read_json(f"{HERMES_DIR}/cron/jobs.json") or {}
    jobs = d.get("jobs", d) if isinstance(d, dict) else d
    out = []
    now = datetime.now(TZ)
    for j in jobs or []:
        if not j.get("enabled", True) or j.get("paused"):
            continue
        nr = j.get("next_run_at")
        if not nr:
            continue
        try:
            t = datetime.fromisoformat(nr).astimezone(TZ)
        except Exception:
            continue
        if t < now:
            continue
        out.append((t, j.get("name") or j.get("id")))
    return sorted(out)[:n]


LIVE_JSON = f"{HERMES_DIR}/plugin-data/kindle-status/live.json"
PLATFORM_ZH.update({"cli": "终端", "tui": "终端", "cron": "定时任务", "a2a": "A2A", "local": "本地"})


def _db_sessions():
    """Turn leases + titles from state.db (read-only, best effort)."""
    import sqlite3
    out = {}
    try:
        c = sqlite3.connect(f"file:{HERMES_DIR}/state.db?mode=ro", uri=True, timeout=2)
        now = datetime.now().timestamp()
        for conv, holder, acq, exp in c.execute(
                "select conversation_id, holder, acquired_at, expires_at from session_turn_leases"):
            if exp < now:
                continue
            plat = holder.rsplit("platform=", 1)[-1] if "platform=" in holder else ""
            turn = holder.split(":turn=", 1)[-1].split(":", 1)[0] if ":turn=" in holder else conv
            out[turn] = {"platform": plat, "state": "running", "since": acq, "lease": True}
        ids = list(out)
        if ids:
            q = ",".join("?" * len(ids))
            for sid, title, src, model in c.execute(
                    f"select id, title, source, model from sessions where id in ({q})", ids):
                out[sid].update({"title": title or "", "platform": out[sid]["platform"] or src,
                                 "model": (model or "").split("[")[0]})
        c.close()
    except Exception:
        pass
    return out


def _db_titles(ids):
    import sqlite3
    try:
        c = sqlite3.connect(f"file:{HERMES_DIR}/state.db?mode=ro", uri=True, timeout=2)
        q = ",".join("?" * len(ids))
        rows = dict((a, (b, s)) for a, b, s in c.execute(f"select id, title, source from sessions where id in ({q})", list(ids)))
        c.close()
        return rows
    except Exception:
        return {}


def live_sessions(max_age=1800):
    """Merged view: plugin hook state (tool / approval / input) + DB turn leases.
    Returns list of dicts sorted with blocking states first."""
    now = datetime.now().timestamp()
    plug = (read_json(LIVE_JSON) or {}).get("sessions", {})
    db = _db_sessions()
    merged = {}
    for sid, s in plug.items():
        if s.get("state") == "idle" and sid not in db:
            continue
        if now - s.get("updated", 0) > max_age and sid not in db:
            continue
        merged[sid] = dict(s)
    for sid, s in db.items():
        m = merged.setdefault(sid, {})
        for k, v in s.items():
            m.setdefault(k, v)
        if m.get("state") == "idle":
            m["state"] = "running"
    missing = [sid for sid, m in merged.items() if not m.get("title")]
    if missing:
        for sid, (title, src) in _db_titles(missing).items():
            merged[sid]["title"] = title or ""
            merged[sid].setdefault("platform", src)
    order = {"approval": 0, "input": 1, "running": 2}
    out = [dict(m, id=sid) for sid, m in merged.items()]
    out.sort(key=lambda m: (order.get(m.get("state"), 3), -(m.get("since") or 0)))
    return out


BOARD = os.environ.get("BOARD_FILE", "/board/board.json")


def board():
    try:
        with open(BOARD) as f:
            return json.load(f)
    except Exception:
        return []


def wrap(d, text, fnt, width, max_lines):
    lines, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=fnt) > width:
            lines.append(cur)
            cur = ch
            if len(lines) == max_lines:
                break
        else:
            cur += ch
    else:
        lines.append(cur)
        return lines
    lines[-1] = lines[-1][:-1] + "…"
    return lines


def stamp():
    """Cheap change token: board content + humidifier state."""
    import hashlib
    try:
        b = open(BOARD, "rb").read()
    except Exception:
        b = b""
    h = "".join((state(e) or {}).get("state", "") for e in (HUMIDIFIER, HEATER))
    return hashlib.sha1(b + h.encode()).hexdigest()[:12]


def sysinfo():
    try:
        load = open("/proc/loadavg").read().split()[:3]
        mi = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        mem = 1 - mi["MemAvailable"] / mi["MemTotal"]
        st = os.statvfs(HERMES_DIR)
        disk = 1 - st.f_bavail / st.f_blocks
        return load, mem, disk
    except Exception:
        return None


def bar(d, x, y, w, h, frac, label=None):
    frac = max(0.0, min(1.0, frac))
    d.rectangle((x, y, x + w, y + h), outline=0, width=2)
    if frac > 0:
        d.rectangle((x + 3, y + 3, x + 3 + int((w - 6) * frac), y + h - 3), fill=40)


def section(d, M, y, title):
    d.text((M, y), title, font=font(44, True), fill=0)


def render(battery=None, charging=None):
    now = datetime.now(TZ)
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    M = 56

    # ---- Header
    d.text((M, 30), now.strftime("%-m月%-d日"), font=font(96, True), fill=0)
    d.text((M + 430, 62), f"星期{WEEKDAY_ZH[now.weekday()]}", font=font(52), fill=0)
    d.text((W - M, 80), f"更新 {now:%H:%M}", font=font(34), fill=90, anchor="ra")
    d.line((M, 160, W - M, 160), fill=0, width=4)

    # ---- Bedroom + outdoor (two columns)
    y = 185
    colw = (W - 2 * M) // 2
    section(d, M, y, "卧室")
    t, h = state(ROOM_TEMP), state(ROOM_HUM)
    if t or h:
        tv = t["state"] if t else "—"
        hv = h["state"] if h else "—"
        tu = t["attributes"].get("unit_of_measurement", "") if t else ""
        try:
            tvf = float(tv)
            if tu == "°F":
                tvf = f2c(tvf)
            tv, tu = f"{tvf:.1f}", "°C"
        except (TypeError, ValueError):
            pass
        d.text((M, y + 70), f"{tv}{tu}", font=font(80, True), fill=0)
        d.text((M + 280, y + 70), f"{hv}%", font=font(80, True), fill=0)
    else:
        d.text((M, y + 85), "温湿度传感器未接入", font=font(40), fill=110)
    for i, (ent, name) in enumerate(((HUMIDIFIER, "加湿"), (HEATER, "取暖"))):
        s = state(ent)
        on = bool(s and s["state"] == "on")
        label = f"{name} 开" if on else (f"{name} 关" if s and s["state"] in ("on", "off") else f"{name} 离线")
        bx = (M + i * 235, y + 180, M + i * 235 + 220, y + 250)
        if on:
            d.rounded_rectangle(bx, 16, fill=0)
        else:
            d.rounded_rectangle(bx, 16, outline=0, width=3)
        d.text(((bx[0] + bx[2]) // 2, (bx[1] + bx[3]) // 2), label, font=font(38, True), fill=255 if on else 0, anchor="mm")

    xo = M + colw + 20
    d.text((xo, y), "室外", font=font(44, True), fill=0)
    w = state(WEATHER)
    if w:
        a = w["attributes"]
        temp = a.get("temperature")
        d.text((xo, y + 65), f"{f2c(temp):.0f}°C", font=font(88, True), fill=0)
        d.text((xo + 250, y + 75), COND_ZH.get(w["state"], w["state"]), font=font(48, True), fill=0)
        d.text((xo + 250, y + 140), f"{temp:.0f}°F · {a.get('humidity', '—')}%", font=font(32), fill=60)
    fc = forecast()[:5]
    if fc:
        fy = y + 200
        cw = (W - M - xo) // len(fc)
        for i, day in enumerate(fc):
            dt = datetime.fromisoformat(day["datetime"]).astimezone(TZ)
            cx = xo + cw * i + cw // 2
            name = "今" if dt.date() == now.date() else WEEKDAY_ZH[dt.weekday()]
            d.text((cx, fy), name, font=font(30, True), fill=0, anchor="ma")
            d.text((cx, fy + 38), COND_ZH.get(day.get("condition"), "?")[:2], font=font(28), fill=0, anchor="ma")
            hi, lo = day.get("temperature"), day.get("templow")
            lo_s = f"/{f2c(lo):.0f}" if lo is not None else ""
            d.text((cx, fy + 76), f"{f2c(hi):.0f}{lo_s}°", font=font(28), fill=40, anchor="ma")

    # ---- Hermes: quota
    y = 520
    d.line((M, y - 20, W - M, y - 20), fill=0, width=4)
    section(d, M, y, "额度")
    q = latest_quota()
    if q:
        cap = datetime.fromisoformat(q["captured_at"]).astimezone(TZ)
        d.text((W - M, y + 6), f"采样 {cap:%H:%M}", font=font(30), fill=90, anchor="ra")
        names = {"A": "GPT·A", "B": "GPT·B", "C": "Claude"}
        ry = y + 70
        bw = 250
        d.text((M + 170, ry - 10), "5小时", font=font(28), fill=90)
        d.text((M + 170 + bw + 130, ry - 10), "本周", font=font(28), fill=90)
        ry += 30
        for k in ("A", "B", "C"):
            acc = q.get("accounts", {}).get(k)
            if not acc:
                continue
            d.text((M, ry), names[k], font=font(38, True), fill=0)
            h5, wk = acc.get("h5_left"), acc.get("week_left")
            x1 = M + 170
            if h5 is not None:
                bar(d, x1, ry + 8, bw, 34, h5 / 100)
                d.text((x1 + bw + 10, ry + 4), f"{h5}%", font=font(32, True), fill=0)
            x2 = x1 + bw + 130
            if wk is not None:
                bar(d, x2, ry + 8, bw, 34, wk / 100)
                d.text((x2 + bw + 10, ry + 4), f"{wk}%", font=font(32, True), fill=0)
            reset = acc.get("week_reset_local") or ""
            d.text((x2, ry + 46), f"重置 {reset}", font=font(24), fill=110)
            ry += 92
        errs = (q.get("errors") or []) + (q.get("claude_errors") or [])
        if errs or q.get("anomalies"):
            d.text((M, ry), f"！异常 {len(errs) + len(q.get('anomalies') or [])} 条，看 Discord", font=font(30, True), fill=0)
    else:
        d.text((M, y + 80), "暂无额度采样", font=font(36), fill=110)

    # ---- Hermes: system
    y = 930
    d.line((M, y - 20, W - M, y - 20), fill=0, width=4)
    section(d, M, y, "Hermes")
    live = live_sessions()
    if live:
        s0 = live[0]
        st = {"approval": "等你批准", "input": "等你回答"}.get(s0.get("state"), "运行中")
        d.text((M, y + 60), f"{st} · {PLATFORM_ZH.get(s0.get('platform') or '', s0.get('platform') or '')} · 共 {len(live)} 个会话",
               font=font(36, True), fill=0)
        sub = s0.get("detail") if s0.get("state") in ("approval", "input") else (s0.get("title") or "")
        d.text((M, y + 106), (sub or "")[:34], font=font(30), fill=40)
    else:
        d.text((M, y + 60), "空闲 · 没有进行中的会话", font=font(36, True), fill=0)
    si = sysinfo()
    if si:
        load, mem, disk = si
        sy = y + 148
        d.text((M, sy), f"负载 {load[0]}", font=font(32), fill=0)
        d.text((M + 230, sy), "内存", font=font(32), fill=0)
        bar(d, M + 310, sy + 6, 180, 30, mem)
        d.text((M + 500, sy), f"{mem*100:.0f}%", font=font(32), fill=0)
        d.text((M + 610, sy), "磁盘", font=font(32), fill=0)
        bar(d, M + 690, sy + 6, 180, 30, disk)
        d.text((M + 880, sy), f"{disk*100:.0f}%", font=font(32), fill=0)
    nj = next_jobs(1)
    if nj:
        t_, name = nj[0]
        when = t_.strftime("%H:%M") if t_.date() == now.date() else t_.strftime("%-m/%-d %H:%M")
        nm = name if len(name) <= 24 else name[:23] + "…"
        d.text((M, y + 194), f"下个任务  {when}  {nm}", font=font(30), fill=30)

    # ---- Hermes message board
    y = 1192
    d.line((M, y - 12, W - M, y - 12), fill=0, width=4)
    d.text((M, y), "Hermes 留言", font=font(40, True), fill=0)
    msgs = board()
    my = y + 56
    if not msgs:
        d.text((M, my), "暂无留言", font=font(30), fill=130)
    f_msg, f_t = font(30), font(24)
    for m in msgs:
        if my > H - 110:
            break
        prefix = "📌 " if m.get("pin") else ""
        d.text((M, my + 4), m.get("t", ""), font=f_t, fill=100)
        lines = wrap(d, prefix.replace("📌 ", "★ ") + m.get("text", ""), f_msg, W - 2 * M - 170, 2)
        for ln in lines:
            d.text((M + 170, my), ln, font=f_msg, fill=0)
            my += 38
        my += 6

    # ---- Footer
    foot = []
    if battery is not None:
        foot.append(f"Kindle {battery}%" + (" 充电中" if charging else ""))
    d.line((M, H - 62, W - M, H - 62), fill=170, width=2)
    d.text((M, H - 18), "  ·  ".join(foot), font=font(28), fill=110, anchor="ls")
    d.text((W - M, H - 18), "按电源键阅读", font=font(28), fill=110, anchor="rs")

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/health":
            body, ctype = b"ok", "text/plain"
        elif u.path == "/stamp":
            body, ctype = stamp().encode(), "text/plain"
        elif u.path.startswith("/n/"):
            if not remote.client_allowed(self.client_address[0]):
                self.send_error(403)
                return
            try:
                out = nativeui.respond(sys.modules[__name__], u.path, urllib.parse.parse_qs(u.query))
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.send_error(500, type(e).__name__)
                return
            if out is None:
                self.send_error(404)
                return
            body, hdrs = out
            self.send_response(200)
            for k, v in hdrs.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        elif u.path == "/app" or u.path.startswith("/app/"):
            if not remote.client_allowed(self.client_address[0]):
                self.send_error(403)
                return
            q = urllib.parse.parse_qs(u.query)
            out = pages.render(u.path[4:], sys.modules[__name__], q.get("m", [""])[0])
            if out is None:
                self.send_error(404)
                return
            body, ctype = out, "text/html; charset=utf-8"
        elif u.path == "/remote":
            if not remote.client_allowed(self.client_address[0]):
                self.send_error(403)
                return
            q = urllib.parse.parse_qs(u.query)
            w = state(WEATHER)
            wc = None
            try:
                wc = f2c(float(w["attributes"]["temperature"])) if w else None
            except Exception:
                pass
            body, ctype = remote.page(state, TZ, wc, q.get("m", [""])[0]), "text/html; charset=utf-8"
        elif u.path in ("/", "/dash.png"):
            q = urllib.parse.parse_qs(u.query)
            bat = q.get("batteryLevel", [None])[0]
            chg = q.get("isCharging", ["false"])[0].lower() in ("1", "true", "yes")
            try:
                bat = int(float(bat)) if bat is not None else None
            except ValueError:
                bat = None
            body, ctype = render(bat, chg), "image/png"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/remote/act" or not remote.client_allowed(self.client_address[0]):
            self.send_error(403)
            return
        n = min(int(self.headers.get("Content-Length") or 0), 4096)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        try:
            ok = remote.act(ha, form.get("a", [""])[0])
            msg = "已执行" if ok else "无效操作"
        except Exception as e:
            msg = f"失败: {type(e).__name__}"
        back = form.get("back", ["/remote"])[0]
        if not (back == "/remote" or back == "/app" or back.startswith("/app/")) or "?" in back or "//" in back:
            back = "/app"
        self.send_response(303)
        self.send_header("Location", back + "?m=" + urllib.parse.quote(msg))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
