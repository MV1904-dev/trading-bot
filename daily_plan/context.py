#!/usr/bin/env python3
"""Context build — vrstvy L1 až L4 zo zadania (L5 je LLM, samostatne).

L1 makro: úrokový diferenciál ECB−Fed a jeho 5-dňová zmena. Zadanie chce
2Y výnosy US−DE; diferenciál politických sadzieb je ich dostupná náhrada
na dennej báze z overených zdrojov, ktoré už máme. Kým nie sú 2Y výnosy
napojené, L1 to hlási ako čiastočný (viď `partial`).

L3 zóny: úrovne bližšie než ZONE_PIPS sa zlučujú a sila zóny = počet
konfluencií, presne podľa zadania.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daily_plan.market import (Bar, atr, load_h1, percentile_in_range,
                               round_numbers, sma, swings, to_d1, to_h4, to_w1)
from lab_regime.engine import Rates

ZONE_PIPS = 15
PIP = 0.0001


# ---------------------------------------------------------------- L1
@dataclass
class Macro:
    bias: str                  # EUR+ | USD+ | neutral
    reason: str
    diff_pa: float | None
    diff_change_5d: float | None
    partial: bool = True       # 2Y výnosy zatiaľ nie sú napojené


def build_macro(rates: Rates, d: date) -> Macro:
    today = d.isoformat()
    prev = (d - timedelta(days=7)).isoformat()
    cur = rates.diff(today)
    old = rates.diff(prev)
    if cur is None:
        return Macro("neutral", "sadzby nedostupné", None, None)
    chg = None if old is None else cur - old

    # Diferenciál sám o sebe je úroveň, nie signál — smer dáva jeho ZMENA.
    # Bez pohybu ostáva bias neutrálny; nechceme obchodovať konštantu.
    if chg is None or abs(chg) < 0.05:
        bias = "neutral"
        why = (f"diferenciál ECB−Fed {cur:+.2f} % p.a. bez zmeny za týždeň "
               f"— sadzby dnes smer nedávajú")
    elif chg > 0:
        bias = "EUR+"
        why = (f"diferenciál ECB−Fed {cur:+.2f} % p.a., za týždeň {chg:+.2f} "
               f"v prospech eura")
    else:
        bias = "USD+"
        why = (f"diferenciál ECB−Fed {cur:+.2f} % p.a., za týždeň {chg:+.2f} "
               f"v prospech dolára")
    return Macro(bias, why, cur, chg)


# ---------------------------------------------------------------- L2
@dataclass
class Structure:
    bias: str                  # bullish | bearish | mixed
    location: str              # stred | horný kraj | dolný kraj
    logic: str                 # range | breakout/rejection
    pct_1y: float | None
    pct_2y: float | None
    sma_d: dict
    sma_w: dict
    stack: str
    notes: list[str] = field(default_factory=list)


def build_structure(d1: list[Bar], w1: list[Bar], price: float) -> Structure:
    sd = {n: sma(d1, n) for n in (20, 100, 200)}
    sw = {n: sma(w1, n) for n in (20, 100, 200)}

    have = [v for v in sd.values() if v]
    if len(have) == 3 and sd[20] > sd[100] > sd[200]:
        stack = "bullish"
    elif len(have) == 3 and sd[20] < sd[100] < sd[200]:
        stack = "bearish"
    else:
        stack = "mixed"

    p1 = percentile_in_range(price, d1, 252)
    p2 = percentile_in_range(price, d1, 504)

    if p1 is None:
        location, logic = "neznáme", "range"
    elif p1 >= 80:
        location, logic = "horný kraj", "breakout/rejection"
    elif p1 <= 20:
        location, logic = "dolný kraj", "breakout/rejection"
    else:
        location, logic = "stred", "range"

    above = sum(1 for v in sd.values() if v and price > v)
    bias = "bullish" if above >= 2 else "bearish" if above <= 1 else "mixed"
    if stack == "mixed" and 40 < (p1 or 50) < 60:
        bias = "mixed"

    notes = [f"cena {price:.5f}, {above}/3 denných SMA pod cenou, stack {stack}"]
    if p1 is not None:
        notes.append(f"percentil 1R {p1:.0f} %, 2R {p2:.0f} %" if p2 is not None
                     else f"percentil 1R {p1:.0f} %")
    return Structure(bias, location, logic, p1, p2, sd, sw, stack, notes)


# ---------------------------------------------------------------- L3
@dataclass
class Zone:
    low: float
    high: float
    sources: list[str]

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    @property
    def strength(self) -> int:
        return len(self.sources)

    def dist_pips(self, price: float) -> float:
        if self.low <= price <= self.high:
            return 0.0
        return (self.low - price if price < self.low else price - self.high) / PIP


def build_zones(d1: list[Bar], w1: list[Bar], h4: list[Bar],
                price: float, structure: Structure) -> tuple[list[Zone], list[Zone]]:
    raw: list[tuple[float, str]] = []

    for lb, tag in ((5, "swing 5d"), (20, "swing 20d"), (60, "swing 60d")):
        hs, ls = swings(d1, lb)
        raw += [(h, tag) for h in hs] + [(l, tag) for l in ls]

    for n, v in structure.sma_d.items():
        if v:
            raw.append((v, f"SMA{n} D1"))
    for n, v in structure.sma_w.items():
        if v:
            raw.append((v, f"SMA{n} W1"))

    if len(d1) >= 2:
        p = d1[-2]
        raw += [(p.h, "predoš. denný high"), (p.l, "predoš. denný low"),
                (p.c, "predoš. denný close")]
    if len(w1) >= 2:
        p = w1[-2]
        raw += [(p.h, "predoš. týž. high"), (p.l, "predoš. týž. low")]

    raw += [(r, "okrúhle číslo") for r in round_numbers(price)]

    # zlúčenie do zón
    raw = [(lvl, src) for lvl, src in raw if lvl and abs(lvl - price) < 0.06]
    raw.sort()
    zones: list[Zone] = []
    for lvl, src in raw:
        if zones and (lvl - zones[-1].high) <= ZONE_PIPS * PIP:
            z = zones[-1]
            z.high = max(z.high, lvl)
            if src not in z.sources:
                z.sources.append(src)
        else:
            zones.append(Zone(lvl, lvl, [src]))

    sup = sorted([z for z in zones if z.high < price],
                 key=lambda z: -z.high)
    res = sorted([z for z in zones if z.low > price], key=lambda z: z.low)
    return sup, res


# ---------------------------------------------------------------- L4
TIER1 = ("non-farm employment", "nfp", "cpi", "fomc", "federal funds",
         "ecb main refinancing", "ecb press conference", "rate decision",
         "monetary policy statement")
TIER2 = ("ism", "adp", "pce", "hicp", "gdp", "jolts", "flash",
         "retail sales", "unemployment claims")


@dataclass
class Event:
    ts: datetime
    currency: str
    title: str
    tier: int


def classify(title: str, impact: str) -> int:
    t = title.lower()
    if any(k in t for k in TIER1):
        return 1
    if any(k in t for k in TIER2):
        return 2
    return 3


def build_events(cal_path: Path, day: date, horizon_days: int = 3) -> list[Event]:
    import json
    try:
        raw = json.loads(cal_path.read_text())
    except Exception:
        return []
    out = []
    lo = datetime.combine(day, datetime.min.time(), timezone.utc)
    hi = lo + timedelta(days=horizon_days)
    for it in raw:
        cur = (it.get("country") or "").upper()
        if cur not in ("USD", "EUR"):
            continue
        try:
            ts = datetime.fromisoformat(it["date"]).astimezone(timezone.utc)
        except Exception:
            continue
        if not (lo <= ts < hi):
            continue
        out.append(Event(ts, cur, it.get("title", "?"),
                         classify(it.get("title", ""), it.get("impact", ""))))
    return sorted(out, key=lambda e: e.ts)
