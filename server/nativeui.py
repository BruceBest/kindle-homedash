"""Server side of the native Kindle app (hdnative client) -- LANDSCAPE layout.

The client shows PNGs and forwards raw touch coordinates. Every page is drawn
here with Pillow and returns its own tap zones, so the whole UI lives on the
OptiPlex and can change without touching the Kindle.

The logical canvas is 1448x1072 (landscape). It is rotated 90 degrees before it
is sent, because the Kindle framebuffer is portrait (1072x1448); taps are mapped
back. Which way the Kindle is turned is set with env LANDSCAPE_PORT:

  right (default)  USB port on the right  -> image rotated clockwise
  left             USB port on the left   -> image rotated counter-clockwise

  GET /n/page?page=home            -> PNG (+ X-Page, X-Stamp headers)
  GET /n/tap?page=..&rx=&ry=&rxmin=&rxmax=&rymin=&rymax=  -> PNG of the resulting page
  GET /n/stamp?page=home           -> change token; client redraws when it changes
"""
import hashlib
import time
import io
import os
from datetime import datetime

from PIL import Image, ImageDraw

import remote

W, H = 1448, 1072          # logical (landscape) canvas
SB = 190                   # left sidebar width (tabs)
CW = W - SB                # content width
M = 30
TOP = 100                  # header height inside the content area
TABS = [("home", "首页"), ("room", "卧室"), ("modes", "模式"), ("weather", "天气"), ("quota", "额度"), ("hermes", "Hermes"), ("board", "留言")]
TITLES = dict(TABS) | {"home": "卧室面板"}
PORT = os.environ.get("LANDSCAPE_PORT", "right")   # right | left

ROOM_TEMP = os.environ.get("ROOM_TEMP_ENTITY", "sensor.0xa4c138294330ffff_temperature")
ROOM_HUM = os.environ.get("ROOM_HUM_ENTITY", "sensor.0xa4c138294330ffff_humidity")


BATT = {"lvl": None, "chg": False, "ts": 0.0}   # last battery report from the Kindle client (query: bat, chg)


def note_battery(q):
    try:
        lvl = int(float(q.get("bat", [""])[0]))
    except (TypeError, ValueError):
        return
    BATT.update(lvl=max(0, min(100, lvl)), chg=q.get("chg", ["0"])[0] in ("1", "true", "True"), ts=time.time())


class Page:
    def __init__(self, ctx, name, msg=""):
        self.ctx, self.name, self.msg = ctx, name, msg
        self.img = Image.new("L", (CW, H), 255)       # content canvas (sidebar added in compose)
        self.d = ImageDraw.Draw(self.img)
        self.zones = []          # content coords (x0, y0, x1, y1, action)
        self.f = ctx.font
        self.full = None

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
        self.text((M, 50), TITLES.get(self.name, self.name), 46, True, anchor="lm")
        if self.name == "home":
            self.d.rounded_rectangle((330, 20, 470, 80), 14, outline=0, width=3)
            self.text((400, 50), "退出", 30, True, anchor="mm")
            self.zone((320, 0, 480, TOP), ("exit",))
        self.text((CW - M, 50), f"{now:%m-%d %H:%M}", 30, False, 60, "rm")
        if BATT["lvl"] is not None and time.time() - BATT["ts"] < 1800:
            lvl = BATT["lvl"]
            bx, by = CW - M - 330, 36            # battery icon: 46x24 body + nub, filled to level
            self.d.rounded_rectangle((bx, by, bx + 46, by + 26), 5, outline=0, width=3)
            self.d.rectangle((bx + 46, by + 8, bx + 50, by + 18), fill=0)
            fw = int(38 * lvl / 100)
            if fw > 0:
                self.d.rectangle((bx + 4, by + 4, bx + 4 + fw, by + 22), fill=0)
            self.text((bx + 62, 50), f"{lvl}%" + (" 充电中" if BATT["chg"] else ""), 28, lvl <= 15 and not BATT["chg"], 0, "lm")
        self.zone((CW - 320, 0, CW, TOP), ("refresh",))
        self.d.line((0, TOP, CW, TOP), fill=0, width=4)
        if self.msg:
            self.d.rounded_rectangle((M, TOP + 12, CW - M, TOP + 68), 14, fill=0)
            self.text((CW // 2, TOP + 40), self.msg, 30, True, 255, "mm")

    @property
    def body_top(self):
        return TOP + (84 if self.msg else 24)

    def compose(self):
        """Sidebar + content on the full landscape canvas; returns zones in full coordinates."""
        full = Image.new("L", (W, H), 255)
        d = ImageDraw.Draw(full)
        full.paste(self.img, (SB, 0))
        d.line((SB, 0, SB, H), fill=0, width=4)
        th = H / len(TABS)
        zones = []
        for i, (k, n) in enumerate(TABS):
            y0, y1 = int(i * th), int((i + 1) * th)
            if k == self.name:
                d.rectangle((0, y0, SB - 1, y1), fill=0)
            elif i:
                d.line((0, y0, SB - 4, y0), fill=0, width=2)
            d.text((SB // 2, (y0 + y1) // 2), n, font=self.f(36, True), fill=255 if k == self.name else 0, anchor="mm")
            zones.append((0, y0, SB, y1, ("nav", k)))
        for x0, y0, x1, y1, a in self.zones:
            zones.append((x0 + SB, y0, x1 + SB, y1, a))
        self.full = full
        self.full_zones = zones

    def png(self):
        if self.full is None:
            self.compose()
        # Kindle framebuffer is portrait: rotate the landscape canvas.
        img = self.full.rotate(-90 if PORT == "right" else 90, expand=True)
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()


# ------------------------------------------------------------ sleep screen
_LOGO = None


def _logo():
    global _LOGO
    if _LOGO is None:
        try:
            _LOGO = Image.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "404-logo-gray.png")).convert("L")
        except OSError:
            _LOGO = False
    return _LOGO or None


def render_sleep(ctx):
    """Static 'asleep' screen: the 404nfound logo + a tiny status line (refreshed rarely by the device)."""
    full = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(full)
    logo = _logo()
    ly = 70
    if logo:
        s = 600
        full.paste(logo.resize((s, s), Image.LANCZOS), ((W - s) // 2, ly))
        ly += s
    d.text((W // 2, ly + 50), "休眠中 · 按电源键唤醒", font=ctx.font(46, True), fill=0, anchor="mm")
    now = datetime.now(ctx.TZ)
    bits = [f"{now:%m-%d %H:%M}"]
    th, hu = _room_c(ctx, ctx.state(ROOM_TEMP)), _num(ctx.state(ROOM_HUM))
    if th is not None:
        bits.append(f"{th:.1f}°C" + (f" {hu:.0f}%" if hu is not None else ""))
    if BATT["lvl"] is not None and time.time() - BATT["ts"] < 7200:
        bits.append(f"Kindle {BATT['lvl']}%")
    d.text((W // 2, ly + 115), "  ·  ".join(bits), font=ctx.font(30, False), fill=110, anchor="mm")
    img = full.rotate(-90 if PORT == "right" else 90, expand=True)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
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
def _room_c(ctx, s):
    try:
        v = float(s["state"])
    except Exception:
        return None
    return ctx.f2c(v) if "F" in str(s.get("attributes", {}).get("unit_of_measurement", "")) else v


def _num(s):
    try:
        return float(s["state"])
    except Exception:
        return None


def page_home(p):
    ctx = p.ctx
    y = p.body_top
    gap = 20
    gw, gh = (CW - 2 * M - 2 * gap) // 3, 230
    tiles = []
    devs = _devices(ctx)
    on = [l for _, l, _, o, _ in devs if o]
    cur = _room_c(ctx, ctx.state(ROOM_TEMP))
    hv = _num(ctx.state(ROOM_HUM))
    dev_line = " · ".join(f"{l}{'开' if o else ('离线' if off else '关')}" for _, l, _, o, off in devs)
    if cur is not None:
        tiles.append(("room", "卧室", f"{cur:.1f}°C" + (f" {hv:.0f}%" if hv is not None else ""), dev_line, bool(on)))
    else:
        tiles.append(("room", "卧室", f"{len(on)} 个开着" if on else "全关", dev_line, bool(on)))
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
        col, row = i % 3, i // 3
        x0 = M + col * (gw + gap)
        y0 = y + row * (gh + gap)
        box = (x0, y0, x0 + gw, y0 + gh)
        p.card(box, dark)
        fg = 255 if dark else 0
        p.text((x0 + 24, y0 + 20), head + "  ›", 28, False, 200 if dark else 70)
        p.text((x0 + 24, y0 + 62), _ellip(p, big, 58, gw - 48, True), 58, True, fg)
        p.text((x0 + 24, y0 + 150), _ellip(p, small, 28, gw - 48), 28, False, fg)
        p.zone(box, ("nav", k))
    y = y + ((len(tiles) + 2) // 3) * (gh + gap) + 6
    p.text((M, y + 46), "快捷开关", 36, True, anchor="lm")
    bx = CW - M
    for key, label, s, o, off in reversed(devs):
        box = (bx - 230, y, bx, y + 92)
        p.button(box, f"{label}{'开' if o else '关'}", o, ("act", f"toggle:{key}"), 34)
        bx -= 250


def _stepper(p, x, y, label, value_txt, minus, plus):
    p.text((x, y + 50), label, 30, True, anchor="lm")
    p.button((x + 150, y, x + 240, y + 100), "−", False, ("act", minus), 60)
    p.text((x + 318, y + 50), value_txt, 40, True, anchor="mm")
    p.button((x + 396, y, x + 486, y + 100), "+", False, ("act", plus), 60)


def page_room(p):
    ctx = p.ctx
    y0 = p.body_top
    lw = 600
    y = y0
    for key, label, s, o, off in _devices(ctx):
        box = (M, y, M + lw, y + 190)
        p.card(box)
        p.text((M + 26, y + 24), label, 48, True)
        if off:
            st = "离线"
        else:
            st = ("开着 · " if o else "关着 · ") + remote._ago(s.get("last_changed", ""), ctx.TZ)
        p.text((M + 26, y + 96), st, 30, False, 50)
        p.button((M + lw - 250, y + 40, M + lw - 26, y + 150), "关" if o else "开", o, ("act", f"toggle:{key}"), 46)
        y += 210
    p.text((M, y + 6), "场景", 36, True)
    y += 58
    sw = (lw - 40) // 3
    for i, (k, (name, _)) in enumerate(remote.SCENES.items()):
        x0 = M + i * (sw + 20)
        p.button((x0, y, x0 + sw, y + 110), name, False, ("act", f"scene:{k}"), 36)
    # ---- climate + targets (right column)
    cx0 = M + lw + 30
    box = (cx0, y0, CW - M, y0 + 700)
    p.card(box)
    x = cx0 + 24
    th, hu = ctx.state(ROOM_TEMP), ctx.state(ROOM_HUM)
    cur, hv = _room_c(ctx, th), _num(hu)
    p.text((x, y0 + 24), "室内", 32, True)
    p.text((x + 92, y0 + 14), f"{cur:.1f}°C" if cur is not None else "—", 62, True)
    p.text((CW - M - 24, y0 + 44), f"湿度 {hv:.0f}%" if hv is not None else "湿度 —", 34, True, anchor="ra")
    p.d.line((cx0 + 20, y0 + 120, CW - M - 20, y0 + 120), fill=0, width=3)
    tv = _num(ctx.state(remote.TARGET_ENTITY))
    _stepper(p, x, y0 + 150, "目标温度", f"{tv:.1f}°C" if tv is not None else "—", "target:-0.5", "target:+0.5")
    ton = (ctx.state(remote.AUTO_ENTITY) or {}).get("state") == "on"
    p.button((x, y0 + 270, x + 486, y0 + 350), "自动恒温 " + ("开" if ton else "关"), ton, ("act", "auto:toggle"), 32)
    p.d.line((cx0 + 20, y0 + 385, CW - M - 20, y0 + 385), fill=190, width=2)
    hv_t = _num(ctx.state(remote.HTARGET_ENTITY))
    _stepper(p, x, y0 + 415, "目标湿度", f"{hv_t:.0f}-{hv_t + 5:.0f}%" if hv_t is not None else "—", "htarget:-5", "htarget:+5")
    hon = (ctx.state(remote.HAUTO_ENTITY) or {}).get("state") == "on"
    p.button((x, y0 + 535, x + 486, y0 + 615), "自动加湿 " + ("开" if hon else "关"), hon, ("act", "hauto:toggle"), 32)
    if tv is not None and hv_t is not None:
        p.text((x, y0 + 635), f"加湿：低于 {hv_t:.0f}% 开 · 高于 {hv_t + 5:.0f}% 关", 24, False, 90)


def _dew_point(t, rh):
    import math
    a, b = 17.62, 243.12
    g = math.log(max(rh, 1) / 100.0) + a * t / (b + t)
    return b * g / (a - g)


def _current_mode(ctx):
    tv = _num(ctx.state(remote.TARGET_ENTITY))
    hv = _num(ctx.state(remote.HTARGET_ENTITY))
    ha_ = (ctx.state(remote.AUTO_ENTITY) or {}).get("state") == "on"
    hh_ = (ctx.state(remote.HAUTO_ENTITY) or {}).get("state") == "on"
    for k, (_, _, t, h, want_heat, want_hum, _off) in remote.MODES.items():
        if tv is not None and hv is not None and abs(tv - t) < 0.01 and abs(hv - h) < 0.01 and ha_ == want_heat and hh_ == want_hum:
            return k
    return None


def _mini(p, x, y, txt, minus, plus):
    p.button((x, y, x + 90, y + 100), "−", False, ("act", minus), 60)
    p.text((x + 215, y + 50), txt, 40, True, anchor="mm")
    p.button((x + 340, y, x + 430, y + 100), "+", False, ("act", plus), 60)


def page_modes(p):
    ctx = p.ctx
    y = p.body_top
    cur_key = _current_mode(ctx)
    sel = remote.EDIT["key"] if remote.EDIT["key"] in remote.MODES else None
    gap = 14
    gw, gh = (CW - 2 * M - 2 * gap) // 3, 168
    tiles = list(remote.MODES.items())
    if len(tiles) < remote.MAX_MODES:
        tiles.append((None, None))
    for i, (k, v) in enumerate(tiles):
        col, row = i % 3, i // 3
        x0, y0 = M + col * (gw + gap), y + row * (gh + gap)
        box = (x0, y0, x0 + gw, y0 + gh)
        if k is None:
            p.card(box)
            p.text(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), "＋ 新增模式", 44, True, anchor="mm")
            p.zone(box, ("act", "madd:new"))
            continue
        name, sub, t, h, _ha, _hh, off = v
        dark = k == cur_key
        fg = 255 if dark else 0
        p.card(box, dark)
        p.text((x0 + 22, y0 + 14), name, 46, True, fg)
        line = "暖风机 / 加湿器 关" if off else f"{t:.1f}°C · {h}-{h + 5}%"
        p.text((x0 + 22, y0 + 76), line, 34, True, fg)
        p.text((x0 + 22, y0 + 122), (sub[:10] if off else f"{sub[:9]}  露点{_dew_point(t, h):.0f}°C"), 24, False, 200 if dark else 80)
        p.zone(box, ("act", f"mode:{k}"))
        if dark:
            p.text((x0 + gw - 128, y0 + 38), "当前", 26, True, 255, "rm")
        if not off:
            eb = (x0 + gw - 112, y0 + 12, x0 + gw - 12, y0 + 64)
            on = k == sel
            ol = 255 if dark else 0
            p.d.rounded_rectangle(eb, 12, outline=ol, width=3, fill=(ol if on else None))
            p.text(((eb[0] + eb[2]) // 2, (eb[1] + eb[3]) // 2), "编辑中" if on else "编辑", 26, True,
                   (0 if dark else 255) if on else ol, "mm")
            p.zone(eb, ("act", f"msel:{k}"))
    rows = (len(tiles) + 2) // 3
    ey = y + rows * (gh + gap) + 6
    if sel and remote.editable(sel):
        name, _s, t, h, *_ = remote.MODES[sel]
        box = (M, ey, CW - M, ey + 218)
        p.card(box)
        p.text((M + 24, ey + 14), f"编辑：{name}", 40, True)
        p.text((M + 24 + 40 * (len(name) + 3), ey + 34), "自由改名等完整编辑请用网页版", 22, False, 100, "lm")
        bx = CW - M - 18
        for lab, act, w in (("恢复默认", "mreset:all", 190), ("删除", f"mdel:{sel}", 120), ("改名", f"mrename:{sel}", 120)):
            p.button((bx - w, ey + 12, bx, ey + 70), lab, False, ("act", act), 28)
            bx -= w + 12
        p.text((M + 24, ey + 150), "温度", 28, True, anchor="lm")
        _mini(p, M + 100, ey + 100, f"{t:.1f}°C", f"mset:{sel}:t:-0.5", f"mset:{sel}:t:0.5")
        p.text((M + 600, ey + 150), "湿度", 28, True, anchor="lm")
        _mini(p, M + 676, ey + 100, f"{h}-{h + 5}%", f"mset:{sel}:h:-5", f"mset:{sel}:h:5")
        sy = ey + 238
    else:
        p.text((M, ey + 14), "点模式卡片右上角的“编辑”可调整它的温度/湿度、改名或删除；“＋ 新增模式”添加。", 26, False, 90)
        sy = ey + 62
    sy = min(sy, H - 110)
    th = _room_c(ctx, ctx.state(ROOM_TEMP))
    hu = _num(ctx.state(ROOM_HUM))
    tv = _num(ctx.state(remote.TARGET_ENTITY))
    hv = _num(ctx.state(remote.HTARGET_ENTITY))
    p.d.line((M, sy, CW - M, sy), fill=0, width=3)
    now_txt = f"现在 {th:.1f}°C · {hu:.0f}%" if th is not None and hu is not None else "现在 —"
    if th is not None and hu is not None:
        now_txt += f" · 露点 {_dew_point(th, hu):.0f}°C"
    p.text((M, sy + 18), now_txt, 32, True)
    tgt_txt = f"目标 {tv:.1f}°C · {hv:.0f}-{hv + 5:.0f}%" if tv is not None and hv is not None else "目标 —"
    mode_txt = remote.MODES[cur_key][0] if cur_key else "自定义"
    p.text((CW - M, sy + 18), f"{tgt_txt}  ·  模式：{mode_txt}", 32, False, 40, "ra")


def page_weather(p):
    ctx = p.ctx
    y = p.body_top
    wx = _weather(ctx)
    if not wx:
        p.text((M, y + 40), "天气不可用", 40)
        return
    w, a, c = wx
    p.text((M, y), f"{c:.0f}°C" if c is not None else "—", 150, True)
    p.text((M + 540, y + 40), ctx.COND_ZH.get(w["state"], w["state"]), 56, True, anchor="ra")
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
    ky = y + 210
    for i, (k, v) in enumerate(kv):
        cx, cy = M + (i % 2) * 290, ky + (i // 2) * 130
        p.text((cx, cy), k, 28, False, 80)
        p.text((cx, cy + 38), v, 44, True)
    fx = M + 620
    p.d.line((fx - 20, y, fx - 20, H - 40), fill=0, width=3)
    today = datetime.now(ctx.TZ).date()
    fy = y
    for day in ctx.forecast()[:7]:
        if fy > H - 90:
            break
        dt = datetime.fromisoformat(day["datetime"]).astimezone(ctx.TZ)
        name = "今天" if dt.date() == today else "周" + ctx.WEEKDAY_ZH[dt.weekday()]
        p.text((fx, fy + 10), name, 36, True)
        p.text((fx + 130, fy + 10), ctx.COND_ZH.get(day.get("condition"), day.get("condition") or ""), 34)
        pr = day.get("precipitation_probability")
        if pr:
            p.text((fx + 330, fy + 14), f"降水 {pr}%", 28, False, 70)
        try:
            t = f"{ctx.f2c(day['temperature']):.0f}° / {ctx.f2c(day['templow']):.0f}°"
        except Exception:
            t = "—"
        p.text((CW - M, fy + 10), t, 36, True, anchor="ra")
        fy += 84
        p.d.line((fx, fy - 8, CW - M, fy - 8), fill=190, width=2)


def page_quota(p):
    ctx = p.ctx
    y = p.body_top
    q = ctx.latest_quota()
    if not q:
        p.text((M, y + 40), "暂无额度采样", 40)
        return
    cap = datetime.fromisoformat(q["captured_at"]).astimezone(ctx.TZ)
    p.text((M, y), f"采样 {cap:%m-%d %H:%M} · 每 5 小时", 28, False, 70)
    y += 56
    names = {"A": "GPT · A", "B": "GPT · B", "C": "Claude"}
    gap = 20
    gw = (CW - 2 * M - 2 * gap) // 3
    for i, k in enumerate(("A", "B", "C")):
        a = q.get("accounts", {}).get(k)
        if not a:
            continue
        x0 = M + i * (gw + gap)
        box = (x0, y, x0 + gw, y + 520)
        p.card(box)
        p.text((x0 + 24, y + 22), names[k], 44, True)
        ry = y + 100
        for lab, key in (("5 小时", "h5_left"), ("本周", "week_left")):
            v = a.get(key)
            if v is None:
                continue
            p.text((x0 + 24, ry), lab, 28)
            p.text((x0 + gw - 24, ry - 4), f"{v}%", 44, True, anchor="ra")
            p.bar(x0 + 24, ry + 52, gw - 48, 44, v / 100)
            ry += 130
        yy = ry + 4
        for l, kk in (("5h 重置", "h5_reset_local"), ("周重置", "week_reset_local"), ("banked", "banked")):
            if a.get(kk) not in (None, ""):
                p.text((x0 + 24, yy), _ellip(p, f"{l} {a[kk]}", 24, gw - 48), 24, False, 80)
                yy += 34
    errs = (q.get("errors") or []) + (q.get("claude_errors") or [])
    if errs or q.get("anomalies"):
        p.text((M, y + 540), f"！异常 {len(errs) + len(q.get('anomalies') or [])} 条，看 Discord", 30, True)


def page_hermes(p):
    ctx = p.ctx
    y = p.body_top
    lw = 640
    live = ctx.live_sessions()[:4]
    now = datetime.now().timestamp()
    p.text((M, y), "正在进行", 38, True)
    ly = y + 60
    if not live:
        p.text((M, ly), "没有进行中的会话", 30, False, 100)
    for s in live:
        st = s.get("state")
        dark = st in ("approval", "input")
        box = (M, ly, M + lw, ly + 140)
        p.card(box, dark)
        fg = 255 if dark else 0
        label = {"approval": "等你批准", "input": "等你回答"}.get(st, "运行中")
        p.text((M + 22, ly + 16), f"{label} · {_plat(ctx, s)}", 34, True, fg)
        p.text((M + lw - 22, ly + 20), _dur(now - (s.get("since") or now)), 28, False, 200 if dark else 80, "ra")
        line2 = s.get("detail") if dark else (f"工具 {s['tool']} · " if s.get("tool") else "") + (s.get("title") or s.get("id", ""))
        p.text((M + 22, ly + 80), _ellip(p, line2 or "", 28, lw - 44), 28, False, fg)
        ly += 156
    rx = M + lw + 40
    rw = CW - M - rx
    ry = y
    si = ctx.sysinfo()
    if si:
        load, mem, disk = si
        p.text((rx, ry), "OptiPlex", 38, True)
        p.text((rx + rw, ry + 4), "负载 " + " / ".join(load), 28, False, 50, "ra")
        ry += 64
        for lab, v in (("内存", mem), ("磁盘", disk)):
            p.text((rx, ry + 4), lab, 30)
            p.bar(rx + 100, ry, rw - 230, 40, v)
            p.text((rx + rw, ry + 2), f"{v*100:.0f}%", 34, True, anchor="ra")
            ry += 62
    ry += 10
    p.d.line((rx, ry, rx + rw, ry), fill=0, width=3)
    ry += 18
    p.text((rx, ry), "接下来的定时任务", 38, True)
    ry += 62
    today = datetime.now(ctx.TZ).date()
    for t_, n in ctx.next_jobs(8):
        if ry > H - 60:
            break
        when = t_.strftime("%H:%M") if t_.date() == today else t_.strftime("%-m/%-d %H:%M")
        p.text((rx, ry), when, 30, True)
        p.text((rx + 190, ry), _ellip(p, n, 30, rw - 190), 30)
        ry += 54


def page_board(p):
    ctx = p.ctx
    y = p.body_top
    msgs = ctx.board()
    if not msgs:
        p.text((M, y + 40), "暂无留言", 40, False, 100)
        return
    f = p.f(32)
    for m in msgs:
        if y > H - 120:
            break
        head = ("★ 置顶 · " if m.get("pin") else "") + m.get("t", "")
        p.text((M, y), head, 26, False, 80)
        y += 40
        for ln in ctx.wrap(p.d, m.get("text", ""), f, CW - 2 * M, 3):
            p.text((M, y), ln, 32)
            y += 44
        y += 12
        p.d.line((M, y, CW - M, y), fill=190, width=2)
        y += 16


PAGES = {"home": page_home, "room": page_room, "modes": page_modes, "weather": page_weather,
         "quota": page_quota, "hermes": page_hermes, "board": page_board}


def build(ctx, name, msg=""):
    if name not in PAGES:
        name = "home"
    p = Page(ctx, name, msg)
    PAGES[name](p)
    p.chrome()
    p.compose()
    return p


def stamp(ctx, name):
    """Changes when anything visible on the page changes (clock at 5-min granularity)."""
    now = datetime.now(ctx.TZ)
    parts = [name, f"{now:%H}{now.minute // 5}"]
    parts += [(ctx.state(e) or {}).get("state", "") for _, e, _ in remote.DEVICES]
    if name in ("room", "home", "modes"):
        parts += [(ctx.state(e) or {}).get("state", "") for e in (
            ROOM_TEMP, ROOM_HUM, remote.TARGET_ENTITY, remote.AUTO_ENTITY, remote.HTARGET_ENTITY, remote.HAUTO_ENTITY)]
    parts.append(repr(list(remote.MODES.items())) + str(remote.EDIT["key"]))
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
    """Raw touch (device portrait, normalised by the reported range) -> landscape canvas pixels."""
    def num(k, d=0):
        try:
            return float(q.get(k, [d])[0])
        except (TypeError, ValueError):
            return float(d)
    rx, ry = num("rx"), num("ry")
    nx = (rx - num("rxmin")) / max(1.0, num("rxmax", 1) - num("rxmin"))
    ny = (ry - num("rymin")) / max(1.0, num("rymax", 1) - num("rymin"))
    if PORT == "right":      # image was rotated clockwise
        lx, ly = ny * W, (1 - nx) * H
    else:                    # rotated counter-clockwise
        lx, ly = (1 - ny) * W, nx * H
    return int(lx), int(ly)


def handle_tap(ctx, name, q):
    x, y = map_tap(q)
    p = build(ctx, name)
    hit = None
    for x0, y0, x1, y1, action in reversed(p.full_zones):   # chrome/content zones last -> checked first
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
                msg = "" if ok else "无效操作"
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
    note_battery(q)
    if path == "/n/sleep":
        return render_sleep(ctx), {"Content-Type": "image/png"}
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
