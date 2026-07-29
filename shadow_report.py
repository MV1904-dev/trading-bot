#!/usr/bin/env python3
"""Shadow report — vyhodnotenie AI tieňového posudzovateľa.

Porovná skutočný P/L behu s hypotetickým „AI-filtrovaným" behom
(bez obchodov, ktoré AI vetovala) a zmeria úspešnosť viet: koľko
vetovaných obchodov by naozaj skončilo v strate.

Číta shadow_judgments + trades z DB oboch inštancií. P/L obchodu =
pnl + funding − provízie (net, ako dashboard).

Spustenie: python3 shadow_report.py [--min-n 20]
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INSTANCES = [("IBKR paper", ROOT / "data" / "bot.db"),
             ("cTrader demo", ROOT / "data" / "bot_ctrader.db")]


def q(db: Path, sql: str, args=()):
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(sql, args))
    con.close()
    return rows


def net(r) -> float:
    return ((r["pnl_usd"] or 0) + (r["funding_usd"] or 0)
            - (r["commission_usd"] or 0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-n", type=int, default=20,
                    help="minimálny počet uzavretých posúdených obchodov")
    a = ap.parse_args()

    grand = {"actual": 0.0, "filtered": 0.0, "cost": 0.0, "closed": 0}
    for name, db in INSTANCES:
        js = q(db, "SELECT * FROM shadow_judgments ORDER BY id") \
            if q(db, "SELECT name FROM sqlite_master WHERE name="
                     "'shadow_judgments'") else []
        print("=" * 70)
        print(f"{name}")
        if not js:
            print("  (žiadne posudky)")
            continue

        by_dec: dict[str, int] = {}
        cost = lat = lat_n = 0.0
        for j in js:
            by_dec[j["decision"]] = by_dec.get(j["decision"], 0) + 1
            cost += j["cost_usd"] or 0
            if j["latency_ms"]:
                lat += j["latency_ms"]
                lat_n += 1
        print(f"  posudkov: {len(js)}  "
              f"({', '.join(f'{k}: {v}' for k, v in sorted(by_dec.items()))})")
        print(f"  náklady API: ${cost:.4f} "
              f"(Ø ${cost / max(len(js), 1):.4f}/volanie, "
              f"Ø latencia {lat / max(lat_n, 1):.0f} ms)")

        # spáruj s uzavretými obchodmi
        trades = {r["id"]: r for r in q(db, "SELECT * FROM trades")}
        judged = []
        for j in js:
            t = trades.get(j["trade_id"]) if j["trade_id"] else None
            if t is not None and t["status"] == "closed":
                judged.append((j, t))
        print(f"  uzavreté posúdené obchody: {len(judged)}")
        if len(judged) < a.min_n:
            print(f"  ⚠ menej než --min-n {a.min_n} — čísla nižšie ber "
                  f"ako predbežné")

        per_strat: dict[str, dict] = {}
        for j, t in judged:
            s = per_strat.setdefault(t["strategy"], {
                "actual": 0.0, "filtered": 0.0, "n": 0,
                "veto_n": 0, "veto_pnl": 0.0, "veto_correct": 0,
                "appr_n": 0, "appr_win": 0})
            p = net(t)
            s["actual"] += p
            s["n"] += 1
            if j["decision"] == "veto":
                s["veto_n"] += 1
                s["veto_pnl"] += p
                s["veto_correct"] += p <= 0
            else:                       # approve aj no_opinion idú do behu
                s["filtered"] += p
                if j["decision"] == "approve":
                    s["appr_n"] += 1
                    s["appr_win"] += p > 0

        for sname, s in per_strat.items():
            print(f"\n  [{sname}]  n={s['n']}")
            print(f"    skutočný P/L:      {s['actual']:+10.2f}")
            print(f"    AI-filtrovaný P/L: {s['filtered']:+10.2f}  "
                  f"(rozdiel {s['filtered'] - s['actual']:+.2f})")
            if s["veto_n"]:
                prec = 100 * s["veto_correct"] / s["veto_n"]
                print(f"    vetá: {s['veto_n']} (P/L vetovaných "
                      f"{s['veto_pnl']:+.2f}; správnych viet {prec:.0f} % — "
                      f"veto je správne, ak obchod skončil ≤ 0)")
            else:
                print("    vetá: žiadne")
            if s["appr_n"]:
                print(f"    approvals: {s['appr_n']} "
                      f"(win rate {100 * s['appr_win'] / s['appr_n']:.0f} %)")
            grand["actual"] += s["actual"]
            grand["filtered"] += s["filtered"]
            grand["closed"] += s["n"]
        grand["cost"] += cost

        # posudky bez exekúcie (blokované/zlyhané vstupy)
        unlinked = sum(1 for j in js if not j["trade_id"])
        if unlinked:
            print(f"\n  posudky bez obchodu (blokované/nenaplnené): {unlinked}")

    print("=" * 70)
    print(f"SPOLU: {grand['closed']} uzavretých posúdených obchodov | "
          f"skutočný {grand['actual']:+.2f} vs AI-filtrovaný "
          f"{grand['filtered']:+.2f} (Δ {grand['filtered'] - grand['actual']:+.2f}) | "
          f"API náklady ${grand['cost']:.4f}")
    verdict = ("AI filter POMÁHA" if grand["filtered"] > grand["actual"]
               else "AI filter NEPOMÁHA (alebo nerozhodne)")
    print(f"VERDIKT (zatiaľ): {verdict} — pozor na malé n.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
