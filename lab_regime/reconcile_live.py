#!/usr/bin/env python3
"""Rekonciliácia: lab engine replay vs skutočné obchody live bota.

Odpovedá na otázku „správa sa bot tak, ako počítal lab?" — prehrá
engine.py (identická mechanika kotvy: min. low od posledného vstupu)
na H1 sviečkach z dumpu bota a porovná vstupy aj cykly s live DB.

Očakávanie: LIVE ≥ LAB replay (live beží na M5, replay na H1, takže
model je spodný odhad). Ak live zaostáva za replayom, niečo blokuje
vstupy (marža, pauza, chyba) a treba ísť do signals logu.

Spustenie na serveri (dáta sú lokálne):
    .venv/bin/python lab_regime/reconcile_live.py
Lokálne: stiahni data/plan_candles.json a data/bot_ctrader_live.db
a odovzdaj cesty cez --candles/--db.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import engine  # noqa: E402
from engine import Cfg  # noqa: E402


class NoRates:
    """Swap ovplyvňuje len čisté €, nie počty obchodov — pri rekonciliácii
    počtov je nula bezpečná náhrada, keď CSV so sadzbami chýbajú."""

    def swap_pa(self, d):  # noqa: D401
        return 0.0, 0.0


def load_bars(path: Path) -> list:
    d = json.loads(path.read_text())
    bars = []
    for c in d["h1"]:
        t = dt.datetime.fromtimestamp(c["time"], dt.timezone.utc)
        bars.append((t.strftime("%Y-%m-%d %H:%M"), c["o"], c["h"], c["l"], c["c"]))
    return bars


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candles", type=Path,
                    default=HERE.parent / "data" / "plan_candles.json")
    ap.add_argument("--db", type=Path,
                    default=HERE.parent / "data" / "bot_ctrader_live.db")
    ap.add_argument("--from", dest="since", default="2026-08-06",
                    help="začiatok okna (deň prvého live obchodu)")
    a = ap.parse_args()

    bars = load_bars(a.candles)
    start = next((i for i, b in enumerate(bars) if b[0] >= a.since), None)
    if start is None:
        sys.exit(f"sviečky nesiahajú po {a.since}")
    print(f"replay: {bars[start][0]} → {bars[-1][0]}  ({len(bars)-start} H1 barov)")

    # vstupy engine nevracia — zalogujeme ich cez podtriedu Pos
    entries: list = []
    base_pos = engine.Pos

    class LoggedPos(base_pos):
        def __init__(self, side, entry, tp, opened, swap=0.0):
            super().__init__(side, entry, tp, opened, swap)
            entries.append((opened, side, entry))

    engine.Pos = LoggedPos
    try:
        rates = engine.Rates() if (HERE / "data" / "ecb_dfr.csv").exists() \
            else NoRates()
        res = engine.run(bars, Cfg(), rates, start, len(bars))
    finally:
        engine.Pos = base_pos

    sim_by_day: dict[str, int] = defaultdict(int)
    for t in res.closed:
        sim_by_day[t["date"][:10]] += 1

    db = sqlite3.connect(a.db)
    db.row_factory = sqlite3.Row
    since_ts = dt.datetime.fromisoformat(a.since) \
        .replace(tzinfo=dt.timezone.utc).timestamp()
    live_entries = db.execute(
        "SELECT ts_open, side, entry_price FROM trades WHERE ts_open >= ? "
        "ORDER BY ts_open", (since_ts,)).fetchall()
    live_by_day: dict[str, int] = defaultdict(int)
    live_closed = 0
    for r in db.execute("SELECT ts_close FROM trades WHERE status='closed' "
                        "AND ts_close >= ?", (since_ts,)):
        live_closed += 1
        day = dt.datetime.fromtimestamp(r["ts_close"], dt.timezone.utc) \
            .strftime("%Y-%m-%d")
        live_by_day[day] += 1

    days = sorted(set(sim_by_day) | set(live_by_day))
    n_days = max(len(days), 1)
    print(f"\n{'':22}{'LAB replay':>12}{'LIVE bot':>12}")
    print(f"{'vstupov':22}{len(entries):>12}{len(live_entries):>12}")
    print(f"{'zavretých cyklov':22}{len(res.closed):>12}{live_closed:>12}")
    print(f"{'cyklov/obch. deň':22}{len(res.closed)/n_days:>12.2f}"
          f"{live_closed/n_days:>12.2f}")

    print("\nzavreté po dňoch:")
    for day in days:
        s, l = sim_by_day.get(day, 0), live_by_day.get(day, 0)
        flag = "" if l >= s else "   ← live ZAOSTÁVA, pozri signals log"
        print(f"  {day}   lab {s}   live {l}{flag}")

    verdict = "OK — live drží krok s modelom" \
        if live_closed >= len(res.closed) else \
        "POZOR — live zaostáva za modelom, treba pozrieť signals (blocked/error)"
    print(f"\nverdikt: {verdict}")


if __name__ == "__main__":
    main()
