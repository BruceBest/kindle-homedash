"""E-ink friendly remote control page (server-rendered HTML, no JS required).

GET  /remote              -> HTML page
POST /remote/act          -> form field `a` = toggle:<key> | scene:<name>; 303 back to /remote

Only whitelisted entities can be touched, and only from LAN / Tailscale / localhost.
Works in the Kindle experimental browser (old WebKit) and on phones.
"""
import html
import ipaddress
import os
from datetime import datetime

DEVICES = [
    # key, entity, label
    ("hum", os.environ.get("HUMIDIFIER_ENTITY", "switch.wo_shi_jia_shi_qi_outlet"), "加湿器"),
    ("heat", os.environ.get("HEATER_ENTITY", "switch.wo_shi_qu_nuan_qi"), "取暖器"),
]
SCENES = {
    "sleep": ("睡前", {"hum": "on", "heat": "on"}),
    "warm": ("只取暖", {"heat": "on"}),
    "off": ("全部关闭", {"hum": "off", "heat": "off"}),
}
ALLOWED_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "100.64.0.0/10", "::1/128", "fd7a:115c:a1e0::/48")]


def client_allowed(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in n for n in ALLOWED_NETS)


def _ago(iso, tz):
    try:
        t = datetime.fromisoformat(iso).astimezone(tz)
    except Exception:
        return ""
    mins = int((datetime.now(tz) - t).total_seconds() // 60)
    if mins < 1:
        return "刚刚"
    if mins < 60:
        return f"{mins} 分钟"
    return f"{mins // 60} 小时 {mins % 60} 分"


def act(ha, action):
    ents = {k: e for k, e, _ in DEVICES}
    kind, _, arg = action.partition(":")
    if kind == "toggle" and arg in ents:
        ha("/api/services/switch/toggle", {"entity_id": ents[arg]})
        return True
    if kind == "scene" and arg in SCENES:
        for k, st in SCENES[arg][1].items():
            ha(f"/api/services/switch/turn_{st}", {"entity_id": ents[k]})
        return True
    return False


CSS = """
body{margin:0;padding:24px;font-family:sans-serif;background:#fff;color:#000}
h1{font-size:44px;margin:0 0 6px}
.sub{font-size:26px;color:#444;margin-bottom:22px}
.card{border:4px solid #000;border-radius:18px;padding:22px;margin-bottom:22px}
.row{display:table;width:100%}
.name{display:table-cell;font-size:46px;font-weight:bold;vertical-align:middle}
.meta{font-size:26px;color:#333;margin-top:6px}
.cell{display:table-cell;text-align:right;vertical-align:middle;width:300px}
button{font-size:42px;font-weight:bold;padding:22px 0;width:280px;border:4px solid #000;border-radius:16px;background:#fff;color:#000}
button.on{background:#000;color:#fff}
.scenes button{width:31%;font-size:36px;margin-right:2%}
.scenes button:last-child{margin-right:0}
.foot{font-size:24px;color:#555;margin-top:18px}
a{color:#000}
"""


def page(state, tz, weather_c=None, msg=""):
    now = datetime.now(tz)
    cards = []
    for key, ent, label in DEVICES:
        s = state(ent)
        st = (s or {}).get("state")
        on = st == "on"
        if s is None or st in ("unavailable", "unknown"):
            status, btn = "离线", "重试"
        else:
            status = ("开着 · " if on else "关着 · ") + _ago(s.get("last_changed", ""), tz)
            btn = "关" if on else "开"
        extra = " · 4 小时自动关" if key == "heat" and on else ""
        cards.append(
            f'<div class="card"><div class="row"><div class="name">{label}'
            f'<div class="meta">{html.escape(status)}{extra}</div></div>'
            f'<div class="cell"><form method="post" action="/remote/act">'
            f'<input type="hidden" name="a" value="toggle:{key}">'
            f'<button class="{"on" if on else ""}" type="submit">{btn}</button></form></div></div></div>')
    scenes = "".join(
        f'<button name="a" value="scene:{k}" type="submit">{v[0]}</button>' for k, v in SCENES.items())
    wx = f" · 室外 {weather_c:.0f}°C" if weather_c is not None else ""
    note = f'<div class="sub"><b>{html.escape(msg)}</b></div>' if msg else ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60;url=/remote">
<title>卧室遥控</title><style>{CSS}</style></head><body>
<h1>卧室遥控</h1><div class="sub">{now:%m-%d %H:%M}{wx} · <a href="/remote">刷新</a></div>{note}
{''.join(cards)}
<div class="card scenes"><div class="meta" style="margin:0 0 14px">场景</div>
<form method="post" action="/remote/act">{scenes}</form></div>
<div class="foot">睡前 = 加湿器 + 取暖器都开；取暖器连续 4 小时会被 HA 自动关闭。</div>
</body></html>""".encode()
