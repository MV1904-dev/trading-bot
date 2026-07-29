#!/usr/bin/env python3
"""Správcovská appka pre t-bota — lokálny web dashboard.

Číta priamo SQLite databázy oboch inštancií (data/bot.db, data/bot_ctrader.db)
a zobrazuje: stav procesov, spojenia a výpadky, otvorené/zatvorené pozície
a vyhodnotenie stratégií (cykly, win rate, náklady, čas držania) vrátane
porovnania s očakávaním z labu.

Spustenie:
    python3 dashboard.py               # http://127.0.0.1:8787
    python3 dashboard.py --port 9000
    python3 dashboard.py --host 0.0.0.0   # prístup aj z mobilu v LAN

Bez závislostí (stdlib). Stránka sa sama obnovuje každých 30 s.
Read-only: dashboard do databáz nikdy nezapisuje.
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
import subprocess
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent

INSTANCES = [
    {"key": "ibkr", "name": "IBKR paper", "db": "data/bot.db",
     "script": "bot.py", "log": "data/bot.log", "ccy": "USD",
     "note": "Grid25-G2B · 25k · cap 20+10"},
    {"key": "ctrader", "name": "cTrader demo", "db": "data/bot_ctrader.db",
     "script": "bot_ctrader.py", "log": "data/bot_ctrader.log", "ccy": "EUR",
     "note": "Grid25-G2B-CT · 2k · cap 20 + G8"},
]

# Očakávania z labu (G2B) na porovnanie
LAB = {"cycles_per_day": 3.5, "cost_ratio": 0.14, "win_rate": 99.0}


# ---------------------------------------------------------------- helpers
def _parse_etime(s: str) -> float:
    """macOS `ps -o etime=` formát: [[dd-]hh:]mm:ss → sekundy."""
    s = s.strip()
    if not s:
        return 0.0
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [int(x) for x in s.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, sec = parts[-3:]
    return days * 86400 + h * 3600 + m * 60 + sec


def ps_running(script: str) -> tuple[bool, int, float]:
    """(beží, pid, uptime_s) — presná zhoda na názov skriptu na konci cmdline.
    macOS `ps` nepozná `etimes`, preto uptime doťahujeme cez `etime`."""
    try:
        out = subprocess.run(["ps", "-eo", "pid,command"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return False, 0, 0.0
    for line in out.splitlines()[1:]:
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        pid, cmd = parts
        cmd = cmd.rstrip()
        if not cmd.endswith(script):
            continue
        # vylúč grep/editor a pod. — musí to byť python proces
        if "python" not in cmd.lower():
            continue
        try:
            et = subprocess.run(["ps", "-p", pid, "-o", "etime="],
                                capture_output=True, text=True,
                                timeout=5).stdout
            return True, int(pid), _parse_etime(et)
        except Exception:  # noqa: BLE001
            return True, int(pid), 0.0
    return False, 0, 0.0


def q(db: Path, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = list(con.execute(sql, args))
        con.close()
        return rows
    except sqlite3.Error:
        return []


def fmt_dur(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def fmt_ts(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%d.%m. %H:%M:%S")


def last_price(db: Path) -> tuple[float | None, float | None]:
    """Posledná známa cena zo signálov (dashboard nemá vlastný feed)."""
    rows = q(db, "SELECT price, ts FROM signals WHERE price > 0 "
                 "ORDER BY id DESC LIMIT 1")
    return (rows[0]["price"], rows[0]["ts"]) if rows else (None, None)


def collect(inst: dict) -> dict:
    db = ROOT / inst["db"]
    running, pid, uptime = ps_running(inst["script"])
    px, px_ts = last_price(db)

    open_rows = q(db, "SELECT * FROM trades WHERE status='open' ORDER BY id")
    closed = q(db, "SELECT * FROM trades WHERE status='closed' "
                   "ORDER BY ts_close DESC")
    events = q(db, "SELECT * FROM events ORDER BY id DESC LIMIT 40")
    sig_stats = q(db, "SELECT action, COUNT(*) n FROM signals GROUP BY action")
    blocked = q(db, "SELECT reason, COUNT(*) n FROM signals WHERE "
                    "action='blocked' GROUP BY reason ORDER BY n DESC LIMIT 5")

    # floating z poslednej známej ceny
    floating = 0.0
    expo = 0.0
    for r in open_rows:
        expo += r["qty"]
        if px:
            floating += ((px - r["entry_price"]) if r["side"] == "long"
                         else (r["entry_price"] - px)) * r["qty"]

    # --- vyhodnotenie stratégií -----------------------------------------
    strategies: dict[str, dict] = {}
    for r in closed:
        s = strategies.setdefault(r["strategy"], {
            "closed": 0, "wins": 0, "gross": 0.0, "comm": 0.0, "fund": 0.0,
            "hold_h": 0.0, "first": None, "last": None, "open": 0})
        s["closed"] += 1
        pnl = r["pnl_usd"] or 0.0
        s["gross"] += pnl
        s["wins"] += 1 if pnl > 0 else 0
        s["comm"] += r["commission_usd"] or 0.0
        s["fund"] += r["funding_usd"] or 0.0
        if r["ts_close"] and r["ts_open"]:
            s["hold_h"] += (r["ts_close"] - r["ts_open"]) / 3600
        for k, v in (("first", r["ts_open"]), ("last", r["ts_close"])):
            if v and (s[k] is None or (v < s[k] if k == "first" else v > s[k])):
                s[k] = v
    for r in open_rows:
        strategies.setdefault(r["strategy"], {
            "closed": 0, "wins": 0, "gross": 0.0, "comm": 0.0, "fund": 0.0,
            "hold_h": 0.0, "first": None, "last": None, "open": 0})
        strategies[r["strategy"]]["open"] += 1

    for s in strategies.values():
        days = 0.0
        if s["first"] and s["last"]:
            days = max((s["last"] - s["first"]) / 86400, 0.04)
        s["days"] = days
        s["cpd"] = s["closed"] / days if days else 0.0
        s["win_rate"] = 100.0 * s["wins"] / s["closed"] if s["closed"] else 0.0
        s["net"] = s["gross"] + s["fund"] - s["comm"]
        s["cost_ratio"] = (s["comm"] / s["gross"]) if s["gross"] > 0 else None
        s["avg_hold"] = s["hold_h"] / s["closed"] if s["closed"] else 0.0

    # posledný stav spojenia z logu
    logp = ROOT / inst["log"]
    conn_state, conn_ts = "neznámy", None
    if logp.exists():
        try:
            tail = logp.read_text(errors="replace").splitlines()[-400:]
            for ln in reversed(tail):
                if "pripojen" in ln or "Connected" in ln or "Bot beží" in ln:
                    conn_state, conn_ts = "pripojený", ln[:19]
                    break
                if "odpojen" in ln or "nedostupn" in ln or "Disconnected" in ln:
                    conn_state, conn_ts = "odpojený", ln[:19]
                    break
        except OSError:
            pass

    return {
        "inst": inst, "running": running, "pid": pid, "uptime": uptime,
        "px": px, "px_ts": px_ts, "open": open_rows, "closed": closed,
        "events": events, "floating": floating, "expo": expo,
        "strategies": strategies, "conn_state": conn_state, "conn_ts": conn_ts,
        "sig": {r["action"]: r["n"] for r in sig_stats},
        "blocked": blocked,
    }


# ---------------------------------------------------------------- HTML
CSS = """
:root{--bg:#0f1115;--card:#171a21;--line:#252a34;--fg:#e6e9ef;--dim:#98a1b3;
--pos:#3fbf7f;--neg:#e5615e;--warn:#e0a33e;--acc:#5b9dd9}
@media(prefers-color-scheme:light){:root{--bg:#f5f6f8;--card:#fff;--line:#e2e5ea;
--fg:#1a1d23;--dim:#6b7280;--pos:#12874f;--neg:#c23934;--warn:#a86a10;--acc:#2a6fad}}
*{box-sizing:border-box}
body{margin:0;padding:20px;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:22px 0 8px;color:var(--dim);
text-transform:uppercase;letter-spacing:.06em}
.sub{color:var(--dim);font-size:12px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card h3{margin:0 0 2px;font-size:16px;display:flex;align-items:center;gap:8px}
.note{color:var(--dim);font-size:12px;margin-bottom:10px}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.on{background:var(--pos)}.off{background:var(--neg)}
.kv{display:grid;grid-template-columns:1fr auto;gap:3px 10px;font-size:13px}
.kv .k{color:var(--dim)}
.pos{color:var(--pos)}.neg{color:var(--neg)}.warn{color:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--dim);font-weight:500;border-bottom:1px solid var(--line);
padding:6px 8px;font-size:12px}
td{padding:6px 8px;border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:none}
.right{text-align:right}.mono{font-variant-numeric:tabular-nums}
.tag{font-size:11px;padding:1px 7px;border-radius:20px;background:var(--line);color:var(--dim)}
.scroll{overflow-x:auto}
.ev{font-size:12.5px;padding:4px 0;border-bottom:1px solid var(--line);display:flex;gap:8px}
.ev:last-child{border:none}.ev .t{color:var(--dim);white-space:nowrap;font-variant-numeric:tabular-nums}
.empty{color:var(--dim);font-size:13px;padding:8px 0}
"""


def h(s) -> str:
    return html.escape(str(s))


def num(v: float, dec: int = 2, sign: bool = False) -> str:
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    txt = f"{v:+,.{dec}f}" if sign else f"{v:,.{dec}f}"
    return f'<span class="{cls} mono">{txt}</span>'


def render(data: list[dict]) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    p = [f"<!doctype html><html lang='sk'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1'>",
         "<meta http-equiv='refresh' content='30'>",
         "<title>t-bot dashboard</title>", f"<style>{CSS}</style></head><body>",
         "<h1>🤖 t-bot — správcovský panel</h1>",
         f"<div class='sub'>obnovené {now} · automaticky každých 30 s · "
         f"read-only pohľad do data/*.db</div>"]

    # --- karty inštancií ------------------------------------------------
    p.append("<div class='grid'>")
    for d in data:
        i = d["inst"]
        dot = "on" if d["running"] else "off"
        state = (f"beží · PID {d['pid']} · {fmt_dur(d['uptime'])}"
                 if d["running"] else "NEBEŽÍ")
        conn_cls = "pos" if d["conn_state"] == "pripojený" else "neg"
        px = f"{d['px']:.5f}" if d["px"] else "—"
        p.append(
            f"<div class='card'><h3><span class='dot {dot}'></span>{h(i['name'])}</h3>"
            f"<div class='note'>{h(i['note'])}</div><div class='kv'>"
            f"<span class='k'>proces</span><span>{h(state)}</span>"
            f"<span class='k'>spojenie</span><span class='{conn_cls}'>{h(d['conn_state'])}</span>"
            f"<span class='k'>posl. cena (zo signálov)</span><span class='mono'>{px}</span>"
            f"<span class='k'>otvorené pozície</span><span class='mono'>{len(d['open'])}</span>"
            f"<span class='k'>expozícia</span><span class='mono'>{d['expo']:,.0f}</span>"
            f"<span class='k'>floating (odhad)</span><span>{num(d['floating'], 2, True)} {h(i['ccy'])}</span>"
            f"<span class='k'>signály</span><span class='mono'>"
            f"{d['sig'].get('executed',0)} vykonaných · {d['sig'].get('blocked',0)} blok. · "
            f"{d['sig'].get('error',0)} chýb</span>"
            f"</div></div>")
    p.append("</div>")

    # --- vyhodnotenie stratégií ------------------------------------------
    p.append("<h2>Vyhodnotenie stratégií</h2><div class='card scroll'><table>"
             "<tr><th>stratégia</th><th class='right'>cyklov</th>"
             "<th class='right'>otv.</th><th class='right'>win %</th>"
             "<th class='right'>hrubý P/L</th><th class='right'>provízie</th>"
             "<th class='right'>funding</th><th class='right'>čistý</th>"
             "<th class='right'>cyklov/deň</th><th class='right'>náklady %</th>"
             "<th class='right'>Ø držanie</th></tr>")
    any_s = False
    for d in data:
        for name, s in d["strategies"].items():
            any_s = True
            cpd_cls = "pos" if s["cpd"] >= LAB["cycles_per_day"] * 0.7 else "warn"
            cr = s["cost_ratio"]
            cr_txt = "—"
            if cr is not None:
                cr_cls = "pos" if cr <= 0.25 else "neg"
                cr_txt = f"<span class='{cr_cls} mono'>{100*cr:.1f}%</span>"
            p.append(
                f"<tr><td>{h(name)}<br><span class='tag'>{h(d['inst']['name'])}</span></td>"
                f"<td class='right mono'>{s['closed']}</td>"
                f"<td class='right mono'>{s['open']}</td>"
                f"<td class='right mono'>{s['win_rate']:.0f}%</td>"
                f"<td class='right'>{num(s['gross'], 2, True)}</td>"
                f"<td class='right mono'>−{s['comm']:,.2f}</td>"
                f"<td class='right'>{num(s['fund'], 2, True)}</td>"
                f"<td class='right'>{num(s['net'], 2, True)}</td>"
                f"<td class='right'><span class='{cpd_cls} mono'>{s['cpd']:.2f}</span></td>"
                f"<td class='right'>{cr_txt}</td>"
                f"<td class='right mono'>{s['avg_hold']:.1f} h</td></tr>")
    if not any_s:
        p.append("<tr><td colspan='11' class='empty'>Zatiaľ žiadne obchody.</td></tr>")
    p.append("</table></div>")
    p.append(f"<div class='sub'>lab očakáva ~{LAB['cycles_per_day']:.1f} cyklov/deň "
             f"a náklady ~{100*LAB['cost_ratio']:.0f} % (kill hranica 25 %); "
             f"win rate gridu je z podstaty ~99 % — riziko je vo floating DD, "
             f"nie v úspešnosti.</div>")

    # --- otvorené pozície -------------------------------------------------
    p.append("<h2>Otvorené pozície</h2><div class='card scroll'><table>"
             "<tr><th>#</th><th>inštancia</th><th>smer</th><th class='right'>objem</th>"
             "<th class='right'>vstup</th><th class='right'>TP</th>"
             "<th class='right'>vek</th><th class='right'>funding</th></tr>")
    rows = 0
    for d in data:
        for r in d["open"]:
            rows += 1
            side_cls = "pos" if r["side"] == "long" else "neg"
            p.append(
                f"<tr><td class='mono'>{r['id']}</td>"
                f"<td><span class='tag'>{h(d['inst']['name'])}</span></td>"
                f"<td class='{side_cls}'>{h(r['side'].upper())}</td>"
                f"<td class='right mono'>{r['qty']:,.0f}</td>"
                f"<td class='right mono'>{r['entry_price']:.5f}</td>"
                f"<td class='right mono'>{r['tp_price']:.5f}</td>"
                f"<td class='right mono'>{fmt_dur(time.time()-r['ts_open'])}</td>"
                f"<td class='right'>{num(r['funding_usd'] or 0, 2, True)}</td></tr>")
    if not rows:
        p.append("<tr><td colspan='8' class='empty'>Žiadne otvorené pozície.</td></tr>")
    p.append("</table></div>")

    # --- zatvorené ---------------------------------------------------------
    p.append("<h2>Posledné zatvorené obchody</h2><div class='card scroll'><table>"
             "<tr><th>#</th><th>inštancia</th><th>smer</th><th class='right'>vstup</th>"
             "<th class='right'>výstup</th><th class='right'>P/L</th>"
             "<th class='right'>držané</th><th class='right'>zavreté</th></tr>")
    merged = []
    for d in data:
        for r in d["closed"][:15]:
            merged.append((r["ts_close"] or 0, d, r))
    merged.sort(key=lambda x: -x[0])
    if not merged:
        p.append("<tr><td colspan='8' class='empty'>Zatiaľ žiadne zatvorené obchody.</td></tr>")
    for _, d, r in merged[:20]:
        hold = ((r["ts_close"] - r["ts_open"]) if r["ts_close"] else 0)
        p.append(
            f"<tr><td class='mono'>{r['id']}</td>"
            f"<td><span class='tag'>{h(d['inst']['name'])}</span></td>"
            f"<td>{h(r['side'].upper())}</td>"
            f"<td class='right mono'>{r['entry_price']:.5f}</td>"
            f"<td class='right mono'>{(r['close_price'] or 0):.5f}</td>"
            f"<td class='right'>{num(r['pnl_usd'] or 0, 2, True)}</td>"
            f"<td class='right mono'>{fmt_dur(hold)}</td>"
            f"<td class='right mono'>{fmt_ts(r['ts_close'])}</td></tr>")
    p.append("</table></div>")

    # --- udalosti / výpadky -------------------------------------------------
    p.append("<h2>Udalosti a výpadky</h2><div class='grid'>")
    for d in data:
        p.append(f"<div class='card'><h3>{h(d['inst']['name'])}</h3>")
        evs = [e for e in d["events"]][:12]
        if not evs:
            p.append("<div class='empty'>Žiadne udalosti.</div>")
        for e in evs:
            cls = {"alarm": "neg", "warn": "warn"}.get(e["level"], "")
            p.append(f"<div class='ev'><span class='t'>{fmt_ts(e['ts'])}</span>"
                     f"<span class='{cls}'>{h(e['message'][:90])}</span></div>")
        if d["blocked"]:
            p.append("<div class='note' style='margin-top:10px'>Najčastejšie "
                     "dôvody blokovania vstupu:</div>")
            for b in d["blocked"]:
                p.append(f"<div class='ev'><span class='t mono'>{b['n']}×</span>"
                         f"<span>{h((b['reason'] or '')[:80])}</span></div>")
        p.append("</div>")
    p.append("</div>")

    p.append("</body></html>")
    return "".join(p)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api"):
            data = [collect(i) for i in INSTANCES]
            payload = json.dumps([{
                "name": d["inst"]["name"], "running": d["running"],
                "open": len(d["open"]), "closed": len(d["closed"]),
                "floating": round(d["floating"], 2),
                "conn": d["conn_state"],
            } for d in data], ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        body = render([collect(i) for i in INSTANCES]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass          # ticho — nezaplav terminál


def main() -> int:
    ap = argparse.ArgumentParser(description="t-bot dashboard")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 sprístupní aj v LAN (napr. z mobilu)")
    a = ap.parse_args()
    srv = HTTPServer((a.host, a.port), Handler)
    print(f"t-bot dashboard beží na http://{a.host}:{a.port}  (Ctrl-C ukončí)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nUkončené.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
