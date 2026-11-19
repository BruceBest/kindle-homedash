"""Multi-page e-ink "app" (server-rendered HTML, no JS needed).

  /app            summary: tappable tiles, each opens a detail page
  /app/room       bedroom devices, climate targets, scenes (controls)
  /app/modes      one-tap climate modes (beach / day / sleep / away / off)
  /app/weather    current weather + 5-day forecast
  /app/quota      GPT A/B + Claude quota detail
  /app/hermes     gateway, platforms, host load, upcoming cron jobs
  /app/board      Hermes message board

Layout uses tables / inline-block only (the Kindle 5.x browser is an old WebKit).
"""
import html
from datetime import datetime

import nativeui
import remote

TABS = [("", "首页"), ("room", "卧室"), ("modes", "模式"), ("weather", "天气"), ("quota", "额度"), ("hermes", "Hermes"), ("board", "留言")]

CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:sans-serif;background:#fff;color:#000;font-size:28px}
.top{border-bottom:4px solid #000;padding:16px 24px;display:table;width:100%}
.top .t{display:table-cell;font-size:40px;font-weight:bold;vertical-align:middle}
.top .r{display:table-cell;text-align:right;font-size:26px;color:#333;vertical-align:middle}
.top a{color:#000;text-decoration:none}
.main{padding:18px 20px 150px}
.grid{width:100%;border-collapse:collapse;table-layout:fixed}
.tile{vertical-align:top;padding:7px}
.tile a{display:block;min-height:230px;padding:18px 20px;color:#000;text-decoration:none;border:4px solid #000;border-radius:18px}
.tile .h{font-size:28px;color:#444;margin-bottom:6px}
.tile .big{font-size:58px;font-weight:bold;line-height:1.1}
.tile .s{font-size:26px;color:#222;margin-top:6px;line-height:1.3}
.tile.dark a{background:#000}
.tile.dark a,.tile.dark .h,.tile.dark .s{color:#fff}
.card{border:4px solid #000;border-radius:18px;padding:20px;margin-bottom:18px}
.row{display:table;width:100%}
.c{display:table-cell;vertical-align:middle}
.c.r{text-align:right}
.name{font-size:44px;font-weight:bold}
.meta{font-size:26px;color:#333;margin-top:4px}
.h2{font-size:34px;font-weight:bold;margin:8px 0 12px}
button{font-size:40px;font-weight:bold;padding:20px 0;width:260px;border:4px solid #000;border-radius:16px;background:#fff;color:#000}
button.on{background:#000;color:#fff}
button.sm{font-size:30px;padding:12px 0;width:150px}
.scenes button{width:31%;font-size:34px;margin-right:2%}
.scenes button:last-child{margin-right:0}
.bar{border:3px solid #000;height:34px;border-radius:8px;overflow:hidden;background:#fff}
.bar i{display:block;height:100%;background:#333}
table.kv{width:100%;border-collapse:collapse}
table.kv td{padding:12px 6px;border-bottom:2px solid #bbb;font-size:30px}
table.kv td.v{text-align:right;font-weight:bold}
.msg{border-bottom:2px solid #bbb;padding:14px 4px}
.msg .when{font-size:24px;color:#555}
.msg .txt{font-size:32px;margin-top:4px;line-height:1.35}
.note{border:3px dashed #000;border-radius:14px;padding:12px 16px;margin-bottom:16px;font-size:28px;font-weight:bold}
.tabs{position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:4px solid #000;display:table;width:100%;table-layout:fixed}
.tabs a{display:table-cell;text-align:center;padding:22px 0;font-size:30px;color:#000;text-decoration:none;border-right:2px solid #000}
.tabs a:last-child{border-right:0}
.tabs a.cur{background:#000;color:#fff;font-weight:bold}
.stepper{width:100%}
.stepper .l{display:block;font-size:30px;font-weight:bold;margin-bottom:6px}
.stepper .v{display:block;text-align:center;font-size:54px;font-weight:bold;white-space:nowrap}
.stepper form{display:inline;margin:0}
button.step{width:120px;font-size:56px;padding:6px 0}
.modes{width:100%;border-collapse:collapse;table-layout:fixed}
.modes td{padding:7px;vertical-align:top}
.modes button{width:100%;text-align:left;padding:18px 20px;min-height:230px;border-radius:18px;font-weight:normal}
.modes .n{font-size:50px;font-weight:bold;display:block}
.modes .tv{font-size:36px;font-weight:bold;display:block;margin-top:10px}
.modes .sb{font-size:26px;display:block;margin-top:8px}
.main{max-width:1000px;margin:0 auto}
@media (max-width:700px){body{zoom:.58}}
"""


def _e(s):
    return html.escape(str(s))


def _shell(cur, title, body, ctx, refresh=60, msg=""):
    now = datetime.now(ctx.TZ)
    tabs = "".join(
        f'<a class="{"cur" if k == cur else ""}" href="/app{("/" + k) if k else ""}">{n}</a>' for k, n in TABS)
    back = '' if cur == "" else '<a href="/app">‹ </a>'
    note = f'<div class="note">{_e(msg)}</div>' if msg else ""
    here = "/app" + (("/" + cur) if cur else "")
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="{refresh};url={here}">
<title>{_e(title)}</title><style>{CSS}</style></head><body>
<div class="top"><div class="t">{back}{_e(title)}</div><div class="r">{now:%m-%d %H:%M} · <a href="{here}">刷新</a></div></div>
<div class="main">{note}{body}</div>
<div class="tabs">{tabs}</div>
</body></html>""".encode()


def _bar(frac):
    pct = max(0, min(100, int(round((frac or 0) * 100))))
    return f'<div class="bar"><i style="width:{pct}%"></i></div>'


def _weather_now(ctx):
    w = ctx.state(ctx.WEATHER)
    if not w:
        return None
    a = w["attributes"]
    try:
        c = ctx.f2c(float(a.get("temperature")))
    except Exception:
        c = None
    return {"c": c, "cond": ctx.COND_ZH.get(w["state"], w["state"]), "hum": a.get("humidity"),
            "wind": a.get("wind_speed"), "wind_u": a.get("wind_speed_unit", ""),
            "pressure": a.get("pressure"), "p_u": a.get("pressure_unit", ""),
            "feels": a.get("apparent_temperature"), "cloud": a.get("cloud_coverage"),
            "uv": a.get("uv_index"), "dew": a.get("dew_point")}


def _wind(w):
    v = w.get("wind")
    if v is None:
        return None
    if w.get("wind_u") == "mph":
        return f"{float(v) * 1.609:.0f} km/h"
    return f"{v} {w.get('wind_u', '')}"


def _press(w):
    v = w.get("pressure")
    if v is None:
        return None
    if w.get("p_u") == "inHg":
        return f"{float(v) * 33.864:.0f} hPa"
    return f"{v} {w.get('p_u', '')}"


def _device_rows(ctx):
    out = []
    for key, ent, label in remote.DEVICES:
        s = ctx.state(ent)
        st = (s or {}).get("state")
        out.append((key, label, s, st == "on", st in (None, "unavailable", "unknown")))
    return out


def _toggle_form(key, on, back, cls=""):
    return (f'<form method="post" action="/remote/act" style="margin:0">'
            f'<input type="hidden" name="a" value="toggle:{key}"><input type="hidden" name="back" value="{back}">'
            f'<button class="{cls} {"on" if on else ""}" type="submit">{"关" if on else "开"}</button></form>')


# ---------------------------------------------------------------- pages
def summary(ctx, msg=""):
    tiles = []
    # bedroom
    devs = _device_rows(ctx)
    on = [l for _, l, _, o, _ in devs if o]
    cur_c = nativeui._room_c(ctx, ctx.state(nativeui.ROOM_TEMP))
    cur_h = nativeui._num(ctx.state(nativeui.ROOM_HUM))
    big_room = (f"{cur_c:.1f}°C" + (f" {cur_h:.0f}%" if cur_h is not None else "")) if cur_c is not None else (f"{len(on)} 开" if on else "全关")
    tiles.append(("room", "卧室", big_room,
                  " · ".join(f"{l}{'开' if o else ('离线' if off else '关')}" for _, l, _, o, off in devs), bool(on)))
    # weather
    w = _weather_now(ctx)
    if w:
        fc = ctx.forecast()[:1]
        hl = ""
        if fc:
            try:
                hl = f" · {ctx.f2c(fc[0]['temperature']):.0f}/{ctx.f2c(fc[0]['templow']):.0f}°"
            except Exception:
                pass
        tiles.append(("weather", "室外", f"{w['c']:.0f}°C" if w["c"] is not None else "—",
                      f"{w['cond']} · 湿度 {w['hum']}%{hl}", False))
    else:
        tiles.append(("weather", "室外", "—", "天气不可用", False))
    # quota
    q = ctx.latest_quota()
    if q:
        acc = q.get("accounts", {})
        parts = [f"{n} {acc[k].get('week_left')}%" for k, n in (("A", "A"), ("B", "B"), ("C", "Claude"))
                 if acc.get(k) and acc[k].get("week_left") is not None]
        low = min([acc[k].get("week_left", 100) for k in acc if acc[k].get("week_left") is not None] or [100])
        cap = datetime.fromisoformat(q["captured_at"]).astimezone(ctx.TZ)
        tiles.append(("quota", "额度 · 本周剩余", f"最低 {low}%", " · ".join(parts) + f"<br>采样 {cap:%H:%M}", low < 20))
    else:
        tiles.append(("quota", "额度", "—", "暂无采样", False))
    # hermes
    alive, up, down, agents = ctx.gateway()
    nj = ctx.next_jobs(1)
    nxt = ""
    if nj:
        t_, name = nj[0]
        nxt = f"<br>下个 {t_:%H:%M} {_e(name[:14])}"
    tiles.append(("hermes", "Hermes", "运行中" if alive else "离线?",
                  f"{len(up)} 平台在线 · {agents} 会话{nxt}", not alive))
    # board
    msgs = ctx.board()
    first = msgs[0]["text"] if msgs else "暂无留言"
    pinned = sum(1 for m in msgs if m.get("pin"))
    tiles.append(("board", "Hermes 留言", f"{len(msgs)} 条", _e(first[:40]) + (f"<br>置顶 {pinned}" if pinned else ""), False))
    # system
    si = ctx.sysinfo()
    if si:
        load, mem, disk = si
        tiles.append(("hermes", "OptiPlex", f"负载 {load[0]}", f"内存 {mem*100:.0f}% · 磁盘 {disk*100:.0f}%", False))

    cells = []
    for i in range(0, len(tiles), 2):
        row = []
        for k, h, big, s, dark in tiles[i:i + 2]:
            row.append(f'<td class="tile {"dark" if dark else ""}"><a href="/app/{k}"><div class="h">{h} ›</div>'
                       f'<div class="big">{big}</div><div class="s">{s}</div></a></td>')
        if len(row) == 1:
            row.append('<td></td>')
        cells.append("<tr>" + "".join(row) + "</tr>")
    # quick controls under the tiles
    quick = "".join(
        f'<td style="padding:0 8px 0 0">{_toggle_form(k, o, "/app", "sm")}<div class="meta" style="text-align:center">{l}</div></td>'
        for k, l, _, o, _ in devs)
    body = (f'<table class="grid">{"".join(cells)}</table>'
            f'<div class="card" style="margin-top:22px"><div class="row"><div class="c"><div class="h2" style="margin:0">快捷开关</div>'
            f'<div class="meta">点上面的卡片看详情</div></div><div class="c r"><table style="float:right"><tr>{quick}</tr></table></div></div></div>')
    return _shell("", "卧室面板", body, ctx, msg=msg)


def _act_form(action, label, cls="", back="/app/room", extra_style=""):
    return (f'<form method="post" action="/remote/act" style="display:inline;margin:0">'
            f'<input type="hidden" name="a" value="{action}"><input type="hidden" name="back" value="{back}">'
            f'<button class="{cls}" type="submit" style="{extra_style}">{label}</button></form>')


def _climate_cards(ctx, back="/app/room"):
    cur = nativeui._room_c(ctx, ctx.state(nativeui.ROOM_TEMP))
    hum = nativeui._num(ctx.state(nativeui.ROOM_HUM))
    tv = nativeui._num(ctx.state(remote.TARGET_ENTITY))
    hv = nativeui._num(ctx.state(remote.HTARGET_ENTITY))
    aon = (ctx.state(remote.AUTO_ENTITY) or {}).get("state") == "on"
    hon = (ctx.state(remote.HAUTO_ENTITY) or {}).get("state") == "on"
    dew = f" · 露点 {nativeui._dew_point(cur, hum):.0f}°C" if cur is not None and hum is not None else ""
    now = (f'<div class="card"><div class="meta">室内</div><div class="row"><div class="c"><span class="name" style="font-size:72px">'
           f'{cur:.1f}°C</span></div><div class="c r"><span class="name">{hum:.0f}%</span>' if cur is not None and hum is not None else
           '<div class="card"><div class="meta">室内</div><div class="row"><div class="c"><span class="name">—</span></div><div class="c r">')
    now += f'<div class="meta">湿度{dew}</div></div></div></div>'
    def stepper(label, val, unit, minus, plus, auto_action, auto_on, auto_label):
        v = f"{val:.1f}{unit}" if unit == "°C" and val is not None else (f"{val:.0f}{unit}" if val is not None else "—")
        return (f'<div class="card"><div class="stepper"><div class="l">{label}</div><div class="v">'
                f'{_act_form(minus, "−", "step", back)} {v} {_act_form(plus, "+", "step", back)}</div></div>'
                f'<div style="margin-top:14px">{_act_form(auto_action, f"{auto_label} " + ("开" if auto_on else "关"), "on" if auto_on else "", back, "width:100%;font-size:32px")}</div></div>')
    return (now
            + stepper("目标温度", tv, "°C", "target:-0.5", "target:+0.5", "auto:toggle", aon, "自动恒温")
            + stepper("目标湿度", hv, "%", "htarget:-5", "htarget:+5", "hauto:toggle", hon, "自动加湿"))


def modes(ctx, msg=""):
    cur = nativeui._current_mode(ctx)
    cells = []
    items = list(remote.MODES.items())
    for i in range(0, len(items), 2):
        row = []
        for k, (name, sub, t, h, _ha, _hh, off) in items[i:i + 2]:
            line = "暖风机 / 加湿器 关" if off else f"{t:.1f}°C · {h}-{h + 5}%"
            dew = "" if off else f'<span class="sb">露点 {nativeui._dew_point(t, h):.0f}°C</span>'
            tag = '<span class="sb"><b>当前</b></span>' if k == cur else ""
            row.append(f'<td><form method="post" action="/remote/act" style="margin:0"><input type="hidden" name="back" value="/app/modes">'
                       f'<button class="{"on" if k == cur else ""}" name="a" value="mode:{k}" type="submit">'
                       f'<span class="n">{name}</span><span class="tv">{line}</span><span class="sb">{_e(sub)}</span>{dew}{tag}</button></form></td>')
        if len(row) == 1:
            row.append("<td></td>")
        cells.append("<tr>" + "".join(row) + "</tr>")
    th = nativeui._room_c(ctx, ctx.state(nativeui.ROOM_TEMP))
    hu = nativeui._num(ctx.state(nativeui.ROOM_HUM))
    tv = nativeui._num(ctx.state(remote.TARGET_ENTITY))
    hv = nativeui._num(ctx.state(remote.HTARGET_ENTITY))
    now = (f"现在 {th:.1f}°C · {hu:.0f}%" + f" · 露点 {nativeui._dew_point(th, hu):.0f}°C") if th is not None and hu is not None else "现在 —"
    tgt = f"目标 {tv:.1f}°C · {hv:.0f}-{hv + 5:.0f}%" if tv is not None and hv is not None else "目标 —"
    mode = remote.MODES[cur][0] if cur else "自定义"
    body = (f'<table class="modes">{"".join(cells)}</table>'
            f'<div class="card" style="margin-top:14px"><div class="meta" style="font-size:30px">{now}</div>'
            f'<div class="meta" style="font-size:30px">{tgt} · 模式：{mode}</div></div>')
    return _shell("modes", "模式", body, ctx, msg=msg)


def room(ctx, msg=""):
    cards = []
    for key, label, s, on, off in _device_rows(ctx):
        if off:
            status = "离线"
        else:
            status = ("开着 · " if on else "关着 · ") + remote._ago(s.get("last_changed", ""), ctx.TZ)
        extra = ""
        cards.append(f'<div class="card"><div class="row"><div class="c"><div class="name">{label}</div>'
                     f'<div class="meta">{_e(status)}{extra}</div></div>'
                     f'<div class="c r">{_toggle_form(key, on, "/app/room")}</div></div></div>')
    scenes = "".join(f'<button name="a" value="scene:{k}" type="submit">{v[0]}</button>' for k, v in remote.SCENES.items())
    body = ("".join(cards)
            + f'<div class="card scenes"><div class="h2">场景</div><form method="post" action="/remote/act">'
              f'<input type="hidden" name="back" value="/app/room">{scenes}</form>'
              f'<div class="meta" style="margin-top:12px">睡前 = 加湿器 + 取暖器都开</div></div>'
            )
    body = _climate_cards(ctx) + body
    return _shell("room", "卧室", body, ctx, msg=msg)


def weather(ctx, msg=""):
    w = _weather_now(ctx)
    if not w:
        return _shell("weather", "天气", '<div class="card">天气不可用</div>', ctx, 300, msg)
    kv = [("体感", f"{ctx.f2c(w['feels']):.0f}°C" if w.get("feels") is not None else None),
          ("湿度", f"{w['hum']}%" if w.get("hum") is not None else None),
          ("露点", f"{ctx.f2c(w['dew']):.0f}°C" if w.get("dew") is not None else None),
          ("风速", _wind(w)),
          ("云量", f"{w['cloud']}%" if w.get("cloud") is not None else None),
          ("紫外线", w.get("uv")),
          ("气压", _press(w))]
    rows = "".join(f'<tr><td>{k}</td><td class="v">{_e(v)}</td></tr>' for k, v in kv if v is not None)
    now_card = (f'<div class="card"><div class="row"><div class="c"><div class="big" style="font-size:96px;font-weight:bold">'
                f'{w["c"]:.0f}°C</div></div><div class="c r"><div class="name">{_e(w["cond"])}</div></div></div>'
                f'<table class="kv" style="margin-top:10px">{rows}</table></div>')
    fc_rows = []
    today = datetime.now(ctx.TZ).date()
    for day in ctx.forecast()[:7]:
        dt = datetime.fromisoformat(day["datetime"]).astimezone(ctx.TZ)
        name = "今天" if dt.date() == today else "周" + ctx.WEEKDAY_ZH[dt.weekday()]
        try:
            hi, lo = ctx.f2c(day["temperature"]), ctx.f2c(day["templow"])
            t = f"{hi:.0f}° / {lo:.0f}°"
        except Exception:
            t = "—"
        pr = day.get("precipitation_probability")
        fc_rows.append(f'<tr><td>{name} {dt:%-m/%-d}</td><td>{_e(ctx.COND_ZH.get(day.get("condition"), day.get("condition") or ""))}</td>'
                       f'<td>{"" if pr is None else f"降水 {pr}%"}</td><td class="v">{t}</td></tr>')
    body = now_card + f'<div class="card"><div class="h2">未来几天</div><table class="kv">{"".join(fc_rows)}</table></div>'
    return _shell("weather", "天气", body, ctx, 300, msg)


def quota(ctx, msg=""):
    q = ctx.latest_quota()
    if not q:
        return _shell("quota", "额度", '<div class="card">暂无额度采样</div>', ctx, 300, msg)
    cap = datetime.fromisoformat(q["captured_at"]).astimezone(ctx.TZ)
    names = {"A": "GPT · A", "B": "GPT · B", "C": "Claude"}
    cards = []
    for k in ("A", "B", "C"):
        a = q.get("accounts", {}).get(k)
        if not a:
            continue
        h5, wk = a.get("h5_left"), a.get("week_left")
        rows = []
        if h5 is not None:
            rows.append(f'<tr><td style="width:150px">5 小时</td><td>{_bar(h5/100)}</td><td class="v" style="width:110px">{h5}%</td></tr>')
        if wk is not None:
            rows.append(f'<tr><td>本周</td><td>{_bar(wk/100)}</td><td class="v">{wk}%</td></tr>')
        extra = []
        for lab, key in (("5h 重置", "h5_reset_local"), ("周重置", "week_reset_local"), ("banked", "banked")):
            if a.get(key) not in (None, ""):
                extra.append(f"{lab} {_e(a.get(key))}")
        cards.append(f'<div class="card"><div class="h2">{names[k]}</div><table class="kv">{"".join(rows)}</table>'
                     f'<div class="meta" style="margin-top:8px">{" · ".join(extra)}</div></div>')
    errs = (q.get("errors") or []) + (q.get("claude_errors") or [])
    if errs or q.get("anomalies"):
        cards.append(f'<div class="note">异常 {len(errs) + len(q.get("anomalies") or [])} 条，详情看 Discord 采样消息</div>')
    body = f'<div class="meta" style="margin-bottom:12px">采样时间 {cap:%m-%d %H:%M}（每 5 小时自动采样）</div>' + "".join(cards)
    return _shell("quota", "额度", body, ctx, 300, msg)


def hermes(ctx, msg=""):
    alive, up, down, agents = ctx.gateway()
    plat = "".join(f'<tr><td>{_e(ctx.PLATFORM_ZH.get(p, p))}</td><td class="v">在线</td></tr>' for p in up)
    plat += "".join(f'<tr><td>{_e(ctx.PLATFORM_ZH.get(p, p))}</td><td class="v">离线</td></tr>' for p in down)
    body = (f'<div class="card"><div class="row"><div class="c"><div class="name">网关 {"运行中" if alive else "离线?"}</div>'
            f'<div class="meta">活跃会话 {agents}</div></div></div><table class="kv" style="margin-top:10px">{plat}</table></div>')
    si = ctx.sysinfo()
    if si:
        load, mem, disk = si
        body += (f'<div class="card"><div class="h2">OptiPlex</div><table class="kv">'
                 f'<tr><td style="width:150px">负载</td><td colspan="2" class="v">{" / ".join(load)}</td></tr>'
                 f'<tr><td>内存</td><td>{_bar(mem)}</td><td class="v" style="width:110px">{mem*100:.0f}%</td></tr>'
                 f'<tr><td>磁盘</td><td>{_bar(disk)}</td><td class="v">{disk*100:.0f}%</td></tr></table></div>')
    jobs = ctx.next_jobs(8)
    today = datetime.now(ctx.TZ).date()
    jr = "".join(f'<tr><td>{(t_.strftime("%H:%M") if t_.date() == today else t_.strftime("%-m/%-d %H:%M"))}</td>'
                 f'<td class="v" style="font-weight:normal">{_e(n)}</td></tr>' for t_, n in jobs)
    body += f'<div class="card"><div class="h2">接下来的定时任务</div><table class="kv">{jr or "<tr><td>无</td></tr>"}</table></div>'
    return _shell("hermes", "Hermes", body, ctx, 120, msg)


def board_page(ctx, msg=""):
    msgs = ctx.board()
    items = "".join(f'<div class="msg"><div class="when">{"★ 置顶 · " if m.get("pin") else ""}{_e(m.get("t", ""))}</div>'
                    f'<div class="txt">{_e(m.get("text", ""))}</div></div>' for m in msgs)
    body = f'<div class="card">{items or "暂无留言"}</div><div class="meta">Hermes 用 kindle-msg 发消息到这里，面板也会同步显示。</div>'
    return _shell("board", "Hermes 留言", body, ctx, 60, msg)


ROUTES = {"": summary, "room": room, "modes": modes, "weather": weather, "quota": quota, "hermes": hermes, "board": board_page}


def render(sub, ctx, msg=""):
    fn = ROUTES.get(sub.strip("/"))
    return fn(ctx, msg) if fn else None
