"""Server side of the native Kindle app (hdnative client).

The client shows PNGs and forwards raw touch coordinates. Every page is drawn
here with Pillow and returns its own tap zones, so the whole UI lives on the
OptiPlex and can change without touching the Kindle.

  GET /n/page?page=home            -> PNG (+ X-Page, X-Stamp headers)
  GET /n/tap?page=..&rx=&ry=&rxmin=&rxmax=&rymin=&rymax=  -> PNG of the resulting page
  GET /n/stamp?page=home           -> change token; client redraws when it changes

Touch orientation can be fixed with env TOUCH_SWAP / TOUCH_INVX / TOUCH_INVY = 1.
"""
import hashlib
import io
import os
from datetime import datetime

from PIL import Image, ImageDraw

import remote

W, H = 1072, 1448
M = 40
TOP = 120          # header height
TAB_H = 150        # bottom tab bar height
TABS = [("home", "首页"), ("room", "卧室"), ("weather", "天气"), ("quota", "额度"), ("hermes", "Hermes"), ("board", "留言")]
TITLES = dict(TABS) | {"home": "卧室面板"}

SWAP = os.environ.get("TOUCH_SWAP") == "1"
INVX = os.environ.get("TOUCH_INVX") == "1"
INVY = os.environ.get("TOUCH_INVY") == "1"


class Page:
    def __init__(self, ctx, name, msg=""):
        self.ctx, self.name, self.msg = ctx, name, msg
        self.img = Image.new("L", (W, H), 255)
        self.d = ImageDraw.Draw(self.img)
        self.zones = []          # (x0, y0, x1, y1, action)
        self.f = ctx.font

    def zone(self, box, action):
        self.zones.append((*box, action))

    def text(self, xy, s, size, bold=False, fill=0, anchor="la"):
        self.d.text(xy, str(s), font=self.f(size, bold), fill=fill, anchor=anchor)

    def button(self, box, label, on=False, action=None, size=40):
        if on:
            self.d.rounded_rectangle(box, 18, fill=0)
        else:
            self.d.rounded_rectangle(box, 18, outline=0, width=4)
        self.text(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), label, size, True, 255 if on else 0, "mm")
        if action:
            self.zone(box, action)

    def card(self, box, dark=False):
        if dark:
            self.d.rounded_rectangle(box, 22, fill=0)
        else:
            self.d.rounded_rectangle(box, 22, outline=0, width=4)

    def bar(self, x, y, w, h, frac):
        frac = max(0.0, min(1.0, frac or 0))
        self.d.rounded_rectangle((x, y, x + w, y + h), 8, outline=0, width=3)
        if frac > 0.01:
            self.d.rounded_rectangle((x + 4, y + 4, x + 4 + int((w - 8) * frac), y + h - 4), 5, fill=60)

    def chrome(self):
        ctx = self.ctx
        now = datetime.now(ctx.TZ)
        if self.name != "home":
            self.text((M, 60), "‹", 72, True, anchor="lm")
            self.zone((0, 0, 200, TOP), ("nav", "home"))
            self.text((M + 60, 60), TITLES.get(self.name, self.name), 50, True, anchor="lm")
        else:
            self.text((M, 60), TITLES["home"], 50, True, anchor="lm")
            # exit back to the normal Kindle UI
            self.d.rounded_rectangle((330, 28, 470, 92), 14, outline=0, width=3)
            self.text((400, 60), "退出", 32, True, anchor="mm")
            self.zone((320, 0, 480, TOP), ("exit",))
        self.text((W - M, 60), f"{now:%m-%d %H:%M}", 32, False, 60, "rm")
        self.zone((W - 320, 0, W, TOP), ("refresh",))
        self.d.line((0, TOP, W, TOP), fill=0, width=4)
        if self.msg:
            self.d.rounded_rectangle((M, TOP + 14, W - M, TOP + 74), 14, fill=0)
            self.text((W // 2, TOP + 44), self.msg, 32, True, 255, "mm")
        # tab bar
        y0 = H - TAB_H
        self.d.line((0, y0, W, y0), fill=0, width=4)
        tw = W / len(TABS)
        for i, (k, n) in enumerate(TABS):
            x0, x1 = int(i * tw), int((i + 1) * tw)
            if k == self.name:
                self.d.rectangle((x0, y0 + 2, x1, H), fill=0)
            elif i:
                self.d.line((x0, y0, x0, H), fill=0, width=2)
            self.text(((x0 + x1) // 2, y0 + TAB_H // 2), n, 36, True, 255 if k == self.name else 0, "mm")
            self.zone((x0, y0, x1, H), ("nav", k))

    @property
    def body_top(self):
        return TOP + (90 if self.msg else 24)

    def png(self):
        buf = io.BytesIO()
        self.img.save(buf, "PNG", optimize=True)
        return buf.getvalue()


# ----------------------------------------------------------------- helpers
def _devices(ctx):
    out = []
    for key, ent, label in remote.DEVICES:
        s = ctx.state(ent)
        st = (s or {}).get("state")
        out.append((key, label, s, st == "on", st in (None, "unavailable", "unknown")))
    return out


def _weather(ctx):
    w = ctx.state(ctx.WEATHER)
    if not w:
        return None
    a = w["attributes"]
    try:
        c = ctx.f2c(float(a.get("temperature")))
    except Exception:
        c = None
    return w, a, c


def _plat(ctx, s):
    return ctx.PLATFORM_ZH.get(s.get("platform") or "", s.get("platform") or "?")


def _dur(sec):
    sec = int(max(0, sec))
    if sec < 60:
        return f"{sec} 秒"
    if sec < 3600:
        return f"{sec // 60} 分"
    return f"{sec // 3600} 小时 {sec % 3600 // 60} 分"


def _ellip(p, s, size, width, bold=False):
    f = p.f(size, bold)
    if p.d.textlength(s, font=f) <= width:
        return s
    while s and p.d.textlength(s + "…", font=f) > width:
        s = s[:-1]
    return s + "…"


# ----------------------------------------------------------------- pages
def page_home(p):
    ctx = p.ctx
    y = p.body_top
    gw, gh, gap = (W - 2 * M - 20) // 2, 270, 20
    tiles = []
    devs = _devices(ctx)
    on = [l for _, l, _, o, _ in devs if o]
    tiles.append(("room", "卧室", f"{len(on)} 个开着" if on else "全关",
                  " · ".join(f"{l}{'开' if o else ('离线' if off else '关')}" for _, l, _, o, off in devs), bool(on)))
    wx = _weather(ctx)
    if wx:
        w, a, c = wx
        fc = ctx.forecast()[:1]
        hl = ""
        if fc:
            try:
                hl = f" · {ctx.f2c(fc[0]['temperature']):.0f}/{ctx.f2c(fc[0]['templow']):.0f}°"
            except Exception:
                pass
        tiles.append(("weather", "室外", f"{c:.0f}°C" if c is not None else "—",
                      f"{ctx.COND_ZH.get(w['state'], w['state'])} · {a.get('humidity')}%{hl}", False))
    else:
        tiles.append(("weather", "室外", "—", "不可用", False))
    q = ctx.latest_quota()
    if q:
        acc = q.get("accounts", {})
        vals = {k: acc[k].get("week_left") for k in ("A", "B", "C") if acc.get(k) and acc[k].get("week_left") is not None}
        low = min(vals.values()) if vals else None
        tiles.append(("quota", "额度 · 本周", f"最低 {low}%" if low is not None else "—",
                      "  ".join(f"{'Claude' if k == 'C' else k} {v}%" for k, v in vals.items()), low is not None and low < 20))
    else:
        tiles.append(("quota", "额度", "—", "暂无采样", False))
    alive, up, down, agents = ctx.gateway()
    live = ctx.live_sessions()
    blocked = [s for s in live if s.get("state") in ("approval", "input")]
    working = [s for s in live if s.get("state") == "running"]
    if blocked:
        b = blocked[0]
        tiles.append(("hermes", "Hermes · 需要你", "等你批准" if b["state"] == "approval" else "等你回答",
                      f"{_plat(ctx, b)} · {b.get('detail') or b.get('title') or ''}", True))
    elif working:
        w0 = working[0]
        tool = f" · {w0['tool']}" if w0.get("tool") else ""
        tiles.append(("hermes", "Hermes · 工作中", f"{len(working)} 个会话", f"{_plat(ctx, w0)}{tool} · {w0.get('title') or ''}", False))
    else:
        tiles.append(("hermes", "Hermes", "空闲", "没有进行中的会话", False))
    msgs = ctx.board()
    tiles.append(("board", "留言", f"{len(msgs)} 条", msgs[0]["text"] if msgs else "暂无留言", False))
    nj = ctx.next_jobs(1)
    if nj:
        t_, name = nj[0]
        tiles.append(("hermes", "下个任务", f"{t_:%H:%M}", name, False))
    for i, (k, head, big, small, dark) in enumerate(tiles):
        col, row = i % 2, i // 2
        x0 = M + col * (gw + gap)
        y0 = y + row * (gh + gap)
        box = (x0, y0, x0 + gw, y0 + gh)
        p.card(box, dark)
        fg = 255 if dark else 0
        p.text((x0 + 26, y0 + 26), head + "  ›", 32, False, 200 if dark else 70)
        p.text((x0 + 26, y0 + 80), _ellip(p, big, 64, gw - 52, True), 64, True, fg)
        p.text((x0 + 26, y0 + 180), _ellip(p, small, 30, gw - 52), 30, False, fg)
        p.zone(box, ("nav", k))
    # quick toggles
    y = y + 3 * (gh + gap) + 10
    p.text((M, y + 50), "快捷开关", 38, True, anchor="lm")
    bx = W - M
    for key, label, s, o, off in reversed(devs):
        box = (bx - 220, y, bx, y + 100)
        p.button(box, f"{label}{'开' if o else '关'}", o, ("act", f"toggle:{key}"), 34)
        bx -= 240


def page_room(p):
    ctx = p.ctx
    y = p.body_top
    for key, label, s, o, off in _devices(ctx):
        box = (M, y, W - M, y + 200)
        p.card(box)
        p.text((M + 30, y + 30), label, 52, True)
        if off:
            st = "离线"
        else:
            st = ("开着 · " if o else "关着 · ") + remote._ago(s.get("last_changed", ""), ctx.TZ)
        p.text((M + 30, y + 110), st, 32, False, 50)
        if key == "heat":
            p.text((M + 30, y + 152), "安全：连续 4 小时自动关", 26, False, 90)
        p.button((W - M - 300, y + 40, W - M - 30, y + 160), "关" if o else "开", o, ("act", f"toggle:{key}"), 48)
        y += 225
    p.text((M, y + 20), "场景", 40, True)
    y += 80
    sw = (W - 2 * M - 40) // 3
    for i, (k, (name, _)) in enumerate(remote.SCENES.items()):
        x0 = M + i * (sw + 20)
        p.button((x0, y, x0 + sw, y + 120), name, False, ("act", f"scene:{k}"), 38)
    y += 150
    # ---- room climate + thermostat target
    th = ctx.state(os.environ.get("ROOM_TEMP_ENTITY", "sensor.0xa4c138294330ffff_temperature"))
    hu = ctx.state(os.environ.get("ROOM_HUM_ENTITY", "sensor.0xa4c138294330ffff_humidity"))
    tgt = ctx.state(remote.TARGET_ENTITY)
    auto = ctx.state(remote.AUTO_ENTITY)
    cur = _room_c(ctx, th)
    box = (M, y, W - M, y + 290)
    p.card(box)
    p.text((M + 30, y + 22), "室内", 34, True)
    p.text((M + 150, y + 6), f"{cur:.1f}°C" if cur is not None else "—", 80, True)
    hv = (hu or {}).get("state")
    p.text((W - M - 30, y + 36), f"湿度 {float(hv):.0f}%" if hv not in (None, "unknown", "unavailable") else "湿度 —", 46, True, anchor="ra")
    ty = y + 150
    p.text((M + 30, ty + 50), "目标", 34, True, anchor="lm")
    try:
        tv = float(tgt["state"])
    except Exception:
        tv = None
    p.button((M + 140, ty, M + 260, ty + 100), "−", False, ("act", "target:-0.5"), 64)
    p.text((M + 400, ty + 50), f"{tv:.1f}°C" if tv is not None else "—", 62, True, anchor="mm")
    p.button((M + 540, ty, M + 660, ty + 100), "+", False, ("act", "target:+0.5"), 64)
    aon = (auto or {}).get("state") == "on"
    p.button((W - M - 270, ty, W - M - 30, ty + 100), "自动恒温" + ("开" if aon else "关"), aon, ("act", "auto:toggle"), 32)


def _room_c(ctx, s):
    try:
        v = float(s["state"])
    except Exception:
        return None
    return ctx.f2c(v) if "F" in str(s.get("attributes", {}).get("unit_of_measurement", "")) else v


def page_weather(p):
    ctx = p.ctx
    y = p.body_top
    wx = _weather(ctx)
    if not wx:
        p.text((M, y + 40), "天气不可用", 40)
        return
    w, a, c = wx
    p.text((M, y), f"{c:.0f}°C" if c is not None else "—", 150, True)
    p.text((W - M, y + 40), ctx.COND_ZH.get(w["state"], w["state"]), 60, True, anchor="ra")
    kv = []
    if a.get("apparent_temperature") is not None:
        kv.append(("体感", f"{ctx.f2c(a['apparent_temperature']):.0f}°C"))
    if a.get("humidity") is not None:
        kv.append(("湿度", f"{a['humidity']}%"))
    if a.get("wind_speed") is not None:
        ws = float(a["wind_speed"]) * (1.609 if a.get("wind_speed_unit") == "mph" else 1)
        kv.append(("风速", f"{ws:.0f} km/h"))
    if a.get("pressure") is not None:
        pr = float(a["pressure"]) * (33.864 if a.get("pressure_unit") == "inHg" else 1)
        kv.append(("气压", f"{pr:.0f} hPa"))
    if a.get("cloud_coverage") is not None:
        kv.append(("云量", f"{a['cloud_coverage']}%"))
    if a.get("uv_index") is not None:
        kv.append(("紫外线", a["uv_index"]))
    y += 200
    cw = (W - 2 * M) // 3
    for i, (k, v) in enumerate(kv):
        cx, cy = M + (i % 3) * cw, y + (i // 3) * 110
        p.text((cx, cy), k, 28, False, 80)
        p.text((cx, cy + 36), v, 42, True)
    y += 110 * ((len(kv) + 2) // 3) + 20
    p.d.line((M, y, W - M, y), fill=0, width=3)
    y += 20
    today = datetime.now(ctx.TZ).date()
    for day in ctx.forecast()[:7]:
        if y > H - TAB_H - 90:
            break
        dt = datetime.fromisoformat(day["datetime"]).astimezone(ctx.TZ)
        name = "今天" if dt.date() == today else "周" + ctx.WEEKDAY_ZH[dt.weekday()]
        p.text((M, y + 10), name, 38, True)
        p.text((M + 150, y + 10), ctx.COND_ZH.get(day.get("condition"), day.get("condition") or ""), 36)
        pr = day.get("precipitation_probability")
        if pr:
            p.text((M + 470, y + 14), f"降水 {pr}%", 30, False, 70)
        try:
            t = f"{ctx.f2c(day['temperature']):.0f}° / {ctx.f2c(day['templow']):.0f}°"
        except Exception:
            t = "—"
        p.text((W - M, y + 10), t, 38, True, anchor="ra")
        y += 82
        p.d.line((M, y - 8, W - M, y - 8), fill=190, width=2)


def page_quota(p):
    ctx = p.ctx
    y = p.body_top
    q = ctx.latest_quota()
    if not q:
        p.text((M, y + 40), "暂无额度采样", 40)
        return
    cap = datetime.fromisoformat(q["captured_at"]).astimezone(ctx.TZ)
    p.text((M, y), f"采样 {cap:%m-%d %H:%M} · 每 5 小时", 30, False, 70)
    y += 60
    names = {"A": "GPT · A", "B": "GPT · B", "C": "Claude"}
    for k in ("A", "B", "C"):
        a = q.get("accounts", {}).get(k)
        if not a:
            continue
        box = (M, y, W - M, y + 300)
        p.card(box)
        p.text((M + 30, y + 24), names[k], 46, True)
        ry = y + 100
        for lab, key in (("5 小时", "h5_left"), ("本周", "week_left")):
            v = a.get(key)
            if v is None:
                continue
            p.text((M + 30, ry + 4), lab, 32)
            p.bar(M + 190, ry, W - 2 * M - 360, 44, v / 100)
            p.text((W - M - 30, ry + 4), f"{v}%", 38, True, anchor="ra")
            ry += 70
        info = " · ".join(f"{l} {a[kk]}" for l, kk in (("5h 重置", "h5_reset_local"), ("周重置", "week_reset_local"), ("banked", "banked"))
                          if a.get(kk) not in (None, ""))
        p.text((M + 30, y + 250), _ellip(p, info, 26, W - 2 * M - 60), 26, False, 80)
        y += 325
    errs = (q.get("errors") or []) + (q.get("claude_errors") or [])
    if errs or q.get("anomalies"):
        p.text((M, y), f"！异常 {len(errs) + len(q.get('anomalies') or [])} 条，看 Discord", 32, True)


def page_hermes(p):
    ctx = p.ctx
    y = p.body_top
    # ---- live sessions first: what is Hermes doing right now?
    live = ctx.live_sessions()[:3]
    now = datetime.now().timestamp()
    p.text((M, y), "正在进行", 40, True)
    y += 64
    if not live:
        p.text((M, y), "没有进行中的会话", 32, False, 100)
        y += 60
    for s in live:
        st = s.get("state")
        dark = st in ("approval", "input")
        box = (M, y, W - M, y + 150)
        p.card(box, dark)
        fg = 255 if dark else 0
        label = {"approval": "等你批准", "input": "等你回答"}.get(st, "运行中")
        p.text((M + 24, y + 18), f"{label} · {_plat(ctx, s)}", 38, True, fg)
        p.text((W - M - 24, y + 22), _dur(now - (s.get("since") or now)), 30, False, 200 if dark else 80, "ra")
        line2 = s.get("detail") if dark else (f"工具 {s['tool']} · " if s.get("tool") else "") + (s.get("title") or s.get("id", ""))
        p.text((M + 24, y + 86), _ellip(p, line2 or "", 30, W - 2 * M - 48), 30, False, fg)
        y += 168
    y += 6
    si = ctx.sysinfo()
    if si:
        load, mem, disk = si
        p.text((M, y), "OptiPlex", 40, True)
        p.text((W - M, y + 4), "负载 " + " / ".join(load), 32, False, 50, "ra")
        y += 70
        for lab, v in (("内存", mem), ("磁盘", disk)):
            p.text((M, y + 4), lab, 32)
            p.bar(M + 110, y, W - 2 * M - 250, 44, v)
            p.text((W - M, y + 4), f"{v*100:.0f}%", 36, True, anchor="ra")
            y += 70
    y += 10
    p.d.line((M, y, W - M, y), fill=0, width=3)
    y += 20
    p.text((M, y), "接下来的定时任务", 40, True)
    y += 70
    today = datetime.now(ctx.TZ).date()
    for t_, n in ctx.next_jobs(8):
        if y > H - TAB_H - 70:
            break
        when = t_.strftime("%H:%M") if t_.date() == today else t_.strftime("%-m/%-d %H:%M")
        p.text((M, y), when, 32, True)
        p.text((M + 230, y), _ellip(p, n, 32, W - 2 * M - 230), 32)
        y += 58


def page_board(p):
    ctx = p.ctx
    y = p.body_top
    msgs = ctx.board()
    if not msgs:
        p.text((M, y + 40), "暂无留言", 40, False, 100)
        return
    f = p.f(34)
    for m in msgs:
        if y > H - TAB_H - 120:
            break
        head = ("★ 置顶 · " if m.get("pin") else "") + m.get("t", "")
        p.text((M, y), head, 28, False, 80)
        y += 42
        for ln in ctx.wrap(p.d, m.get("text", ""), f, W - 2 * M, 3):
            p.text((M, y), ln, 34)
            y += 46
        y += 14
        p.d.line((M, y, W - M, y), fill=190, width=2)
        y += 18


PAGES = {"home": page_home, "room": page_room, "weather": page_weather,
         "quota": page_quota, "hermes": page_hermes, "board": page_board}


def build(ctx, name, msg=""):
    if name not in PAGES:
        name = "home"
    p = Page(ctx, name, msg)
    PAGES[name](p)
    p.chrome()
    return p


def stamp(ctx, name):
    """Changes when anything visible on the page changes (clock at 5-min granularity)."""
    now = datetime.now(ctx.TZ)
    parts = [name, f"{now:%H}{now.minute // 5}"]
    parts += [(ctx.state(e) or {}).get("state", "") for _, e, _ in remote.DEVICES]
    if name == "room":
        parts += [(ctx.state(e) or {}).get("state", "") for e in (
            os.environ.get("ROOM_TEMP_ENTITY", "sensor.0xa4c138294330ffff_temperature"),
            os.environ.get("ROOM_HUM_ENTITY", "sensor.0xa4c138294330ffff_humidity"),
            remote.TARGET_ENTITY, remote.AUTO_ENTITY)]
    try:
        parts.append(open(ctx.BOARD, "rb").read().decode(errors="ignore"))
    except Exception:
        pass
    q = ctx.latest_quota() or {}
    parts.append(q.get("captured_at", ""))
    live = ctx.live_sessions()
    parts.append(repr([(s.get("id"), s.get("state"), s.get("tool") if name == "hermes" else "") for s in live]))
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def map_tap(q):
    def num(k, d=0):
        try:
            return float(q.get(k, [d])[0])
        except (TypeError, ValueError):
            return float(d)
    rx, ry = num("rx"), num("ry")
    nx = (rx - num("rxmin")) / max(1.0, num("rxmax", 1) - num("rxmin"))
    ny = (ry - num("rymin")) / max(1.0, num("rymax", 1) - num("rymin"))
    if SWAP:
        nx, ny = ny, nx
    if INVX:
        nx = 1 - nx
    if INVY:
        ny = 1 - ny
    return int(nx * W), int(ny * H)


def handle_tap(ctx, name, q):
    x, y = map_tap(q)
    p = build(ctx, name)
    hit = None
    for x0, y0, x1, y1, action in reversed(p.zones):   # chrome zones were added last -> checked first
        if x0 <= x <= x1 and y0 <= y <= y1:
            hit = action
            break
    print(f"tap page={name} raw=({q.get('rx', ['?'])[0]},{q.get('ry', ['?'])[0]}) -> ({x},{y}) hit={hit}", flush=True)
    msg, target = "", name
    if hit:
        kind = hit[0]
        if kind == "nav":
            target = hit[1]
        elif kind == "act":
            try:
                ok = remote.act(ctx.ha, hit[1])
                msg = "已执行" if ok else "无效操作"
            except Exception as e:
                msg = f"失败 {type(e).__name__}"
        elif kind == "refresh":
            msg = ""
        elif kind == "exit":
            target = "__exit__"
    return target, msg


def respond(ctx, path, q):
    """Returns (body, headers) for /n/* or None."""
    name = q.get("page", ["home"])[0]
    if path == "/n/stamp":
        return stamp(ctx, name).encode(), {"Content-Type": "text/plain"}
    if path == "/n/tap":
        name, msg = handle_tap(ctx, name, q)
        if name == "__exit__":
            return b"", {"Content-Type": "text/plain", "X-Exit": "1"}
        p = build(ctx, name, msg)
        return p.png(), {"Content-Type": "image/png", "X-Page": p.name, "X-Stamp": stamp(ctx, p.name)}
    if path == "/n/page":
        p = build(ctx, name)
        return p.png(), {"Content-Type": "image/png", "X-Page": p.name, "X-Stamp": stamp(ctx, p.name)}
    return None
