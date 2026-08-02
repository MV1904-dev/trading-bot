#!/usr/bin/env python3
"""Dátová vrstva Daily Plan Engine — agregácia a odvodené veličiny.

H1 je zdrojová granularita; D1/W1/H4 sa z nej skladajú, aby celý plán
stál na jednom konzistentnom rade a nemiešali sa zdroje s rôznymi
uzávierkami. Denný bar končí 21:00 UTC (NY close), nie o polnoci —
inak by sa „predchádzajúci denný high/low" z §L3 rozchádzal s tým, čo
vidí obchodník na grafe.
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

H1_CSV = Path(__file__).resolve().parent.parent / "lab_regime" / "data" / "eurusd_h1.csv"
DAY_CLOSE_UTC = 21          # NY close — hranica denného baru


@dataclass(frozen=True)
class Bar:
    ts: datetime
    o: float
    h: float
    l: float
    c: float

    @property
    def day(self) -> str:
        return self.ts.strftime("%Y-%m-%d")


PLAN_CANDLES = Path(__file__).resolve().parent.parent / "data" / "plan_candles.json"


def load_live() -> tuple[list[Bar], list[Bar], datetime] | None:
    """H1 a D1 z dumpu, ktorý robí bot. Vracia None, ak dump neexistuje.

    Bot je jediný, kto smie držať spojenie na brokera (Spotware demo
    dovolí jedno app-auth naraz), takže plánovač si dáta neťahá sám.
    """
    import json
    try:
        raw = json.loads(PLAN_CANDLES.read_text())
    except (OSError, ValueError):
        return None

    def conv(rows):
        return [Bar(datetime.fromtimestamp(r["time"], timezone.utc),
                    r["o"], r["h"], r["l"], r["c"]) for r in rows]

    fetched = datetime.fromisoformat(raw["fetched_at"])
    return conv(raw.get("h1", [])), conv(raw.get("d1", [])), fetched


def load_h1(path: Path = H1_CSV) -> list[Bar]:
    out = []
    with path.open() as f:
        for r in csv.DictReader(f):
            ts = datetime.strptime(r["date"], "%Y-%m-%d %H:%M").replace(
                tzinfo=timezone.utc)
            out.append(Bar(ts, float(r["open"]), float(r["high"]),
                           float(r["low"]), float(r["close"])))
    return out


def _bucket(bars: list[Bar], key) -> list[Bar]:
    groups: dict = {}
    for b in bars:
        groups.setdefault(key(b), []).append(b)
    out = []
    for k in sorted(groups):
        g = groups[k]
        out.append(Bar(g[0].ts, g[0].o, max(x.h for x in g),
                       min(x.l for x in g), g[-1].c))
    return out


def to_h4(bars: list[Bar]) -> list[Bar]:
    return _bucket(bars, lambda b: (b.ts.date(), b.ts.hour // 4))


def to_d1(bars: list[Bar]) -> list[Bar]:
    """Denný bar 21:00 → 21:00 UTC. Bar patrí dňu, v ktorom KONČÍ."""
    def key(b: Bar):
        d = b.ts + timedelta(hours=24 - DAY_CLOSE_UTC)
        return d.date()
    return _bucket(bars, key)


def to_w1(bars: list[Bar]) -> list[Bar]:
    d1 = to_d1(bars)
    return _bucket(d1, lambda b: b.ts.isocalendar()[:2])


def sma(bars: list[Bar], n: int) -> float | None:
    if len(bars) < n:
        return None
    return sum(b.c for b in bars[-n:]) / n


def atr(bars: list[Bar], n: int = 14) -> float | None:
    if len(bars) < n + 1:
        return None
    trs = []
    for prev, cur in zip(bars[-n - 1:-1], bars[-n:]):
        trs.append(max(cur.h - cur.l, abs(cur.h - prev.c), abs(cur.l - prev.c)))
    return sum(trs) / len(trs)


def percentile_in_range(price: float, bars: list[Bar], days: int) -> float | None:
    """Kde je cena v rozsahu posledných `days` denných barov (0–100 %)."""
    win = bars[-days:]
    if len(win) < days // 2:
        return None
    lo, hi = min(b.l for b in win), max(b.h for b in win)
    if hi <= lo:
        return None
    return (price - lo) / (hi - lo) * 100


def swings(bars: list[Bar], lookback: int, left: int = 2, right: int = 2
           ) -> tuple[list[float], list[float]]:
    """Fraktálové swing high/low: bod, ktorý má `left` nižších barov vľavo
    a `right` vpravo. Posledné `right` bary sa nepotvrdia — zámerne, inak
    by sa úroveň prekresľovala pod rukou."""
    win = bars[-lookback:]
    highs, lows = [], []
    for i in range(left, len(win) - right):
        seg = win[i - left:i + right + 1]
        if win[i].h == max(s.h for s in seg):
            highs.append(win[i].h)
        if win[i].l == min(s.l for s in seg):
            lows.append(win[i].l)
    return highs, lows


def round_numbers(price: float, span: float = 0.03) -> list[float]:
    """Okrúhle čísla .x000 a .x500 v okolí ceny."""
    out = []
    lo, hi = price - span, price + span
    x = round(lo * 200) / 200          # krok 0.005
    while x <= hi:
        if abs(x * 1000 - round(x * 1000)) < 1e-9:
            out.append(round(x, 5))
        x += 0.005
    return out
