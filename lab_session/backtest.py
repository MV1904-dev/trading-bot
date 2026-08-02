#!/usr/bin/env python3
"""Krok 0 zo zadania — revalidácia session-flow pravidiel A/B/C.

Zadanie označuje tento krok za blokujúci: bez neho sa nemá implementovať
nič. Testujeme presne to, čo je v špecifikácii, bez pridaných filtrov.

Cena v čase T = OPEN baru označeného T. V spojitom FX je to prakticky
close predošlej hodiny; voľba je konzistentná pre vstupy, výstupy aj
pre okno r_us, takže sa nemôže prejaviť ako systematický posun.

Náklady: 0,7 pipu round trip podľa §9 zadania (predpoklad, ktorý má bot
neskôr overovať meraným slippage).
"""

from __future__ import annotations

import csv
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

H1 = Path(__file__).resolve().parent.parent / "lab_regime" / "data" / "eurusd_h1.csv"
COST_PIP_RT = 0.7           # round trip, §9
PIP = 0.0001


def load() -> dict[tuple[str, int], float]:
    """(YYYY-MM-DD, hodina) → cena v tom čase (open baru)."""
    px: dict[tuple[str, int], float] = {}
    with H1.open() as f:
        for r in csv.DictReader(f):
            d, hm = r["date"].split(" ")
            px[(d, int(hm[:2]))] = float(r["open"])
    return px


def trading_days(px) -> list[str]:
    days = sorted({d for d, _ in px})
    return [d for d in days if date.fromisoformat(d).weekday() < 5]


@dataclass
class Trade:
    day: str
    rule: str
    side: int          # +1 long, −1 short
    entry: float
    exit: float

    @property
    def gross_bp(self) -> float:
        """Hrubý výnos v bázických bodoch (1 bp = 0,01 %)."""
        return self.side * math.log(self.exit / self.entry) * 10_000

    @property
    def net_bp(self) -> float:
        return self.gross_bp - COST_PIP_RT * PIP / self.entry * 10_000


def r_us(px, d: str) -> float | None:
    """Návratnosť US okna 14:00 → 21:00 UTC dňa d."""
    a, b = px.get((d, 14)), px.get((d, 21))
    if a is None or b is None:
        return None
    return math.log(b / a)


def run(px, days: list[str], rules=("A", "B", "C")) -> list[Trade]:
    out: list[Trade] = []
    prev_day = {d: days[i - 1] for i, d in enumerate(days) if i > 0}

    for d in days:
        # --- A: short 07:00 → 12:00 -------------------------------------
        if "A" in rules:
            e, x = px.get((d, 7)), px.get((d, 12))
            if e and x:
                out.append(Trade(d, "A", -1, e, x))

        # --- B: long 00:00 → 07:00, ak včerajšie US okno bolo záporné ----
        # „Včerajšie" = predošlý OBCHODNÝ deň, takže piatok armuje pondelok.
        if "B" in rules and d in prev_day:
            r = r_us(px, prev_day[d])
            if r is not None and r < 0:
                e, x = px.get((d, 0)), px.get((d, 7))
                if e and x:
                    out.append(Trade(d, "B", +1, e, x))

        # --- C: long 16:00 → 20:00 --------------------------------------
        if "C" in rules:
            e, x = px.get((d, 16)), px.get((d, 20))
            if e and x:
                out.append(Trade(d, "C", +1, e, x))
    return out


def stats(trades: list[Trade]) -> dict:
    if not trades:
        return {"n": 0}
    g = [t.gross_bp for t in trades]
    n = [t.net_bp for t in trades]
    mean_g, mean_n = statistics.mean(g), statistics.mean(n)
    sd = statistics.pstdev(g) or 1e-9
    return {
        "n": len(trades),
        "gross_bp": mean_g,
        "net_bp": mean_n,
        "hit": sum(1 for x in g if x > 0) / len(g) * 100,
        "t": mean_g / (sd / math.sqrt(len(g))),
        "sum_net_bp": sum(n),
    }


def by_year(trades: list[Trade]) -> dict[int, dict]:
    buckets = defaultdict(list)
    for t in trades:
        buckets[int(t.day[:4])].append(t)
    return {y: stats(v) for y, v in sorted(buckets.items())}


def equity_unlevered(trades: list[Trade], days: list[str]) -> list[tuple[str, float]]:
    """Nelevrovaná krivka: 1 jednotka notional na obchod, net bp."""
    per_day = defaultdict(float)
    for t in trades:
        per_day[t.day] += t.net_bp / 10_000
    eq, v = [], 1.0
    for d in days:
        v *= (1 + per_day.get(d, 0.0))
        eq.append((d, v))
    return eq


def perf(eq: list[tuple[str, float]]) -> dict:
    if len(eq) < 2:
        return {}
    vals = [v for _, v in eq]
    years = len(vals) / 252
    cagr = (vals[-1] ** (1 / years) - 1) * 100
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    sd = statistics.pstdev(rets) or 1e-9
    sharpe = statistics.mean(rets) / sd * math.sqrt(252)
    peak, dd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak)
    return {"cagr": cagr, "sharpe": sharpe, "maxdd": dd * 100, "years": years}
