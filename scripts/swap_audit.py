#!/usr/bin/env python3
"""Audit swapových nákladov — kedy sa naozaj účtovali a kam ich DB zapísala.

    ssh hetzner '/home/marian/trading-bot/.venv/bin/python \
        /home/marian/trading-bot/scripts/swap_audit.py'

Prečo to treba: bot zapisuje celý naakumulovaný swap obchodu JEDNÝM riadkom
k dátumu ZATVORENIA (bot_ctrader.py, _finalize_close → add_funding s
datetime.now()). Tabuľka `funding` teda nehovorí, kedy sa náklad účtoval,
ale kedy obchod skončil. Preto to v prehľade vyzerá, že niektorý deň v týždni
je výrazne drahší.

Skript preto náklad rozpočíta sám: z ts_open/ts_close dopočíta, cez koľko
rollover nocí obchod naozaj visel, ktoré z nich boli trojnásobné, a podľa
toho rozdelí funding_usd. Porovná to s tým, čo ukazuje `funding.day`.

Predpoklady (dajú sa prepnúť prepínačmi):
* rollover je o 21:00 UTC (= 17:00 New York, bežné pre EURUSD),
* účtuje sa každú noc pondelok–piatok, v stredu trojnásobne. Za týždeň to
  dá 1+1+3+1+1 = 7 nocí na 7 kalendárnych dní — presne preto trojnásobok
  existuje, priemer za týždeň teda sedí; nesedí rozloženie v rámci týždňa.
* trojnásobok je v stredu (cTrader to hlási v poli swapRollover3Days —
  over si ho, broker ho môže mať inak).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

DNI = ["pondelok", "utorok", "streda", "štvrtok", "piatok", "sobota", "nedeľa"]


def rollover_nights(ts_open: float, ts_close: float, hour: int,
                    dows: set[int], triple_dow: int) -> list[tuple[datetime, int]]:
    """Rollover okamihy v intervale (ts_open, ts_close] ako (čas, váha)."""
    start = datetime.fromtimestamp(ts_open, tz=timezone.utc)
    end = datetime.fromtimestamp(ts_close, tz=timezone.utc)
    out: list[tuple[datetime, int]] = []
    cur = start.replace(hour=hour, minute=0, second=0, microsecond=0)
    if cur <= start:
        cur += timedelta(days=1)
    while cur <= end:
        if cur.weekday() in dows:
            out.append((cur, 3 if cur.weekday() == triple_dow else 1))
        cur += timedelta(days=1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", nargs="?", help="cesta k SQLite bota (inak sa odvodí z .env)")
    ap.add_argument("--hour", type=int, default=21, help="hodina rolloveru v UTC (default 21)")
    ap.add_argument("--triple-dow", type=int, default=2,
                    help="deň trojnásobného swapu, 0=pondelok (default 2 = streda)")
    ap.add_argument("--no-friday", action="store_true",
                    help="neúčtovať piatkovú noc (ak ju broker vynecháva)")
    ap.add_argument("--days", type=int, default=0,
                    help="obmedziť na posledných N dní (0 = všetko)")
    args = ap.parse_args()

    db = args.db
    if not db:
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        env = os.path.join(root, ".env")
        demo = False
        if os.path.exists(env):
            with open(env) as f:
                demo = any(l.strip() == "CTRADER_DEMO=1" for l in f)
        db = os.path.join(root, "data",
                          "bot_ctrader.db" if demo else "bot_ctrader_live.db")
    if not os.path.exists(db):
        print(f"CHYBA: DB {db} neexistuje.")
        return 1

    dows = {0, 1, 2, 3} if args.no_friday else {0, 1, 2, 3, 4}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    where = "status='closed' AND ts_close IS NOT NULL AND funding_usd != 0"
    if args.days:
        where += f" AND ts_close >= {datetime.now(timezone.utc).timestamp() - args.days*86400}"
    trades = con.execute(f"SELECT * FROM trades WHERE {where} ORDER BY ts_close").fetchall()

    print(f"DB: {db}")
    print(f"rollover {args.hour}:00 UTC, trojnásobok: {DNI[args.triple_dow]}, "
          f"noci: {', '.join(DNI[d] for d in sorted(dows))}")
    print(f"zatvorené obchody so swapom: {len(trades)}\n")
    if not trades:
        print("Žiadne obchody so swapom — nie je čo auditovať.")
        return 0

    # --- 1) kam DB zapísala náklad vs. kedy naozaj vznikol ------------------
    podla_zavretia: dict[int, float] = defaultdict(float)
    podla_noci: dict[int, float] = defaultdict(float)
    per_unit: dict[str, list[float]] = defaultdict(list)
    bez_noci = 0
    nespravny_den = 0

    for t in trades:
        swap = t["funding_usd"]
        close_dt = datetime.fromtimestamp(t["ts_close"], tz=timezone.utc)
        podla_zavretia[close_dt.weekday()] += swap

        noci = rollover_nights(t["ts_open"], t["ts_close"],
                               args.hour, dows, args.triple_dow)
        vahy = sum(w for _, w in noci)
        if not vahy:
            bez_noci += 1
            podla_noci[close_dt.weekday()] += swap
            continue
        for dt, w in noci:
            podla_noci[dt.weekday()] += swap * w / vahy
        per_unit[t["side"]].append(swap / vahy)
        if noci[-1][0].date() != close_dt.date():
            nespravny_den += 1

    def tabulka(nazov: str, data: dict[int, float]) -> None:
        print(nazov)
        spolu = sum(data.values()) or 1.0
        for d in range(7):
            if d not in data:
                continue
            podiel = data[d] / spolu * 100
            bar = "█" * int(round(abs(podiel) / 3))
            print(f"  {DNI[d]:<9} {data[d]:>9.2f}  {podiel:>5.1f} %  {bar}")
        print(f"  {'SPOLU':<9} {sum(data.values()):>9.2f}\n")

    tabulka("A) Ako to vyzerá v DB — podľa dňa ZATVORENIA obchodu:", podla_zavretia)
    tabulka("B) Ako náklad naozaj vznikal — rozpočítaný na rollover noci:", podla_noci)

    # --- 2) implikovaná sadzba za jednu noc --------------------------------
    print("C) Implikovaný swap na jednu (jednoduchú) noc:")
    for side in ("long", "short"):
        vals = per_unit.get(side, [])
        if not vals:
            print(f"  {side:<6} — žiadne dáta")
            continue
        vals.sort()
        med = vals[len(vals) // 2]
        print(f"  {side:<6} obchodov {len(vals):>4}  medián {med:>8.4f} USD/noc  "
              f"min {vals[0]:>8.4f}  max {vals[-1]:>8.4f}")
    print()

    # --- 3) diagnostika -----------------------------------------------------
    print("D) Nálezy:")
    if nespravny_den:
        print(f"  ⚠️  {nespravny_den} z {len(trades)} obchodov má swap zapísaný ku dňu "
              f"zatvorenia, hoci posledná účtovaná noc bola skôr.")
        print("      → tabuľka `funding` NIE JE časovým radom nákladu.")
    if bez_noci:
        print(f"  ⚠️  {bez_noci} obchodov má swap, hoci podľa ts_open/ts_close "
              f"neprešli žiadnou rollover nocou — skontroluj hodinu rolloveru "
              f"(--hour) alebo časové pečiatky.")
    trojnasobok = podla_noci.get(args.triple_dow, 0.0)
    spolu = sum(podla_noci.values())
    if spolu:
        print(f"  • {DNI[args.triple_dow]} nesie {trojnasobok/spolu*100:.1f} % nákladu "
              f"(pri rovnomernom držaní sa čaká ~{3/(len(dows)+2)*100:.0f} %).")
    print("  • bot_ctrader.py::_swap_cost_eur_day triedi strany do tierov podľa "
          "JEDNEJ noci a trojnásobok ignoruje. Za celý týždeň priemer sedí "
          f"({len(dows)+2} nocí na 7 dní), ale pozícia, ktorá visí práve cez "
          f"{DNI[args.triple_dow]}jšiu noc, zaplatí 3× toľko, koľko tier "
          "predpokladal — strana v tieri ≤0,50 €/deň stojí v tú noc až 1,50 €, "
          "teda nad hranicou, pri ktorej sa má vypínať.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
