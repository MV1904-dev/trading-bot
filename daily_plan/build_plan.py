#!/usr/bin/env python3
"""Denný cyklus kroky 1–5: dáta → kontext → scenáre → risk → plán.

Spúšťa sa o 21:30 UTC (Ne–Št). Krok 6 (executor) a 7 (journal) sú
samostatné — executor zámerne nesmie byť v tom istom procese, aby sa
plán nedal zmeniť za behu.

Použitie:
    python3 -m daily_plan.build_plan [--equity 5000] [--date 2026-08-03]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daily_plan import emit, scenarios
from daily_plan.context import build_events, build_macro, build_structure, build_zones
from daily_plan.market import (atr, load_h1, load_live, to_d1,
                               to_h4, to_w1)
from lab_regime.engine import Rates

ROOT = Path(__file__).resolve().parent.parent
PLANS = ROOT / "daily_plan" / "plans"
CALENDAR = ROOT / "data" / "ff_calendar.json"


def next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--equity", type=float, default=None,
                    help="prebije virtuálnu equity z denníka (na testy)")
    ap.add_argument("--date", help="obchodný deň (default: najbližší ďalší)")
    ap.add_argument("--stdout", action="store_true",
                    help="vypíš plán, nezapisuj (na skúšku)")
    ap.add_argument("--push", action="store_true",
                    help="pošli plán do Supabase na schválenie")
    a = ap.parse_args()

    live = load_live()
    if live:
        h1, d1_live, fetched = live
        source = f"živé dáta z bota ({fetched:%Y-%m-%d %H:%M} UTC)"
    else:
        h1, d1_live, fetched = load_h1(), None, None
        source = "statické CSV (bot ešte nedumpol sviečky)"
    if not h1:
        print("žiadne dáta", file=sys.stderr)
        return 1
    # D1 od brokera má správne uzávierky; z H1 skladáme len ako záložku
    d1 = d1_live if d1_live else to_d1(h1)
    w1, h4 = to_w1(h1), to_h4(h1)
    price = h1[-1].c
    atr_d1 = atr(d1, 14) or 0.0
    atr_avg = (sum(atr(d1[:i], 14) or 0 for i in range(-60, 0)) / 60) or atr_d1

    day = date.fromisoformat(a.date) if a.date else next_trading_day(
        datetime.now(timezone.utc).date())

    # Objem sa počíta z VIRTUÁLNEJ equity Daily Planu (vklad + vlastné
    # P/L), nie zo zostatku účtu — ten hýbe grid a 0,5 % by prestalo
    # byť 0,5 %.
    if a.equity is None:
        from daily_plan.journal import Journal
        a.equity = Journal().virtual_equity()

    macro = build_macro(Rates(), day)
    structure = build_structure(d1, w1, price)
    sup, res = build_zones(d1, w1, h4, price, structure)
    events = build_events(CALENDAR, day)

    scns = scenarios.build(price, atr_d1, atr_avg, macro, structure,
                           sup, res, events, a.equity, day)

    plan = emit.to_json(day, price, atr_d1, macro, structure, sup, res,
                        events, scns, a.equity, news=None)

    from daily_plan.narrative import build_narrative
    plan["narrative"] = build_narrative(plan)
    plan["data_source"] = source
    staleness = (datetime.now(timezone.utc) - h1[-1].ts).days
    if staleness > 2:
        plan["WARNING_stale_data"] = (
            f"posledný bar je {staleness} dní starý ({h1[-1].ts:%Y-%m-%d %H:%M} "
            f"UTC) — plán NIE JE použiteľný na obchodovanie")

    if a.stdout:
        print(emit.to_md(plan))
        if "WARNING_stale_data" in plan:
            print(f"\n> ⚠ {plan['WARNING_stale_data']}")
        return 0

    j, m = emit.write(plan, PLANS)
    print(f"zapísané: {j.name}, {m.name}")
    if a.push:
        from daily_plan.push import push
        push(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
