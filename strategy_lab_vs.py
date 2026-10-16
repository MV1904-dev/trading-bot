#!/usr/bin/env python3
"""Lab: kandidáti na súperiacu stratégiu proti Grid25-G2B-CT.

Zadanie 16. 10. 2026: vlastná stratégia, ktorá pobeží na virtuálnom účte
s vernou simuláciou cTradera a bude súťažiť so živým gridom.

Engine je prísnejší než strategy_lab_ladder.py a zodpovedá brokerovi:
* marža z väčšej strany (hedging cTradera, overené 15. 10. 2026),
* stop-out pri 50 % — broker zatvára najstratovejšiu pozíciu, kým je
  úroveň marže znova nad 50 %; účet tým prežije, ale strata je reálna,
* spread 0,1 pipu, provízia 0,526 EUR na cyklus, swap z trading/rates.py
  (rollover 21:00 UTC, streda 3×) — kalibrované na live obchody,
* TP a SL vnútri sviečky; keď sviečka zasiahne oba, počíta sa SL
  (poradie cien v sviečke nepoznáme, konzervatívne).

Metriky: realizovaný čistý zisk, výsledok vrátane floatu, maximálny
prepad equity (od vrcholu), minimálna úroveň marže a počet zásahov
stop-outu. Rozhodujúci je pomer výsledok / maximálny prepad.

    python3 strategy_lab_vs.py            # 2 roky + live obdobie
"""

from __future__ import annotations

import csv
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from trading.rates import funding_rates_pa  # noqa: E402

DATA = ROOT / "data" / "lab_ladder" / "EURUSD_M5_20241015_20261015.csv"
QTY = 10_000.0
SPREAD = 0.00001
COMMISSION = 0.526
LEVERAGE = 30.0
STOP_OUT = 50.0
CAPITAL = 15_200.0
LIVE_START = datetime(2026, 8, 6, tzinfo=timezone.utc).timestamp()


def load() -> list[tuple]:
    with DATA.open() as f:
        return [tuple(float(x) for x in r) for r in csv.reader(f)]


# --- účet --------------------------------------------------------------------

@dataclass
class P:
    side: str
    entry: float
    tp: float | None
    sl: float | None
    bar: int
    tag: str = ""


@dataclass
class Acct:
    name: str
    capital: float = CAPITAL
    pos: list[P] = field(default_factory=list)
    realized: float = 0.0
    comm: float = 0.0
    swap: float = 0.0
    cycles: int = 0
    wins: int = 0
    losses: int = 0
    stopouts: int = 0
    peak: float = CAPITAL
    max_dd: float = 0.0
    min_ml: float = math.inf
    last_eq: float = CAPITAL
    _last_t: float | None = None

    def n(self, side: str) -> int:
        return sum(p.side == side for p in self.pos)

    def open(self, side: str, bid: float, tp, sl, i: int, tag="") -> None:
        entry = bid + SPREAD if side == "long" else bid
        self.pos.append(P(side, entry, tp, sl, i, tag))
        self.comm += COMMISSION

    def close(self, p: P, price: float) -> None:
        self.pos.remove(p)
        diff = price - p.entry if p.side == "long" else p.entry - price
        pnl = diff * QTY / price
        self.realized += pnl
        self.cycles += 1
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1

    def exit_price(self, p: P, bid: float) -> float:
        return bid if p.side == "long" else bid + SPREAD

    def floating(self, bid: float) -> float:
        ask = bid + SPREAD
        return sum((bid - p.entry) * QTY / bid if p.side == "long"
                   else (p.entry - ask) * QTY / ask for p in self.pos)

    def used(self) -> float:
        nl = self.n("long")
        return max(nl, len(self.pos) - nl) * QTY / LEVERAGE

    def equity(self, bid: float) -> float:
        return self.capital + self.realized - self.comm + self.swap + self.floating(bid)

    def rollover(self, t: float) -> None:
        prev, self._last_t = self._last_t, t
        if prev is None or not self.pos:
            return
        d = datetime.fromtimestamp(prev, timezone.utc).replace(
            hour=21, minute=0, second=0, microsecond=0)
        if d.timestamp() <= prev:
            d += timedelta(days=1)
        while d.timestamp() <= t:
            if d.weekday() < 5:
                long_pa, short_pa = funding_rates_pa(d.strftime("%Y-%m-%d"))
                mult = 3 if d.weekday() == 2 else 1
                for p in self.pos:
                    pa = -long_pa if p.side == "long" else short_pa
                    self.swap += pa / 100 / 365 * QTY * mult
            d += timedelta(days=1)

    def stop_out(self, bid: float) -> None:
        """Broker zatvára najstratovejšiu pozíciu, kým marža < 50 %."""
        while self.pos:
            used = self.used()
            if used <= 0 or self.equity(bid) / used * 100 >= STOP_OUT:
                return
            worst = min(self.pos, key=lambda p: (
                (bid - p.entry) if p.side == "long" else (p.entry - bid - SPREAD)))
            self.close(worst, self.exit_price(worst, bid))
            self.stopouts += 1

    def mark(self, bid: float) -> None:
        eq = self.equity(bid)
        self.last_eq = eq
        self.peak = max(self.peak, eq)
        self.max_dd = max(self.max_dd, self.peak - eq)
        used = self.used()
        if used > 0:
            self.min_ml = min(self.min_ml, eq / used * 100)

    def row(self) -> dict:
        net = self.realized - self.comm + self.swap
        res = self.last_eq - self.capital
        nl = self.n("long")
        return {
            "stratégia": self.name,
            "cyklov": self.cycles,
            "W/L": f"{self.wins}/{self.losses}",
            "realiz.": round(net),
            "swap": round(self.swap),
            "výsledok": round(res),
            "max prepad": round(self.max_dd),
            "výsl/prepad": round(res / self.max_dd, 2) if self.max_dd else None,
            "min marža": f"{self.min_ml:.0f} %" if self.min_ml < math.inf else "—",
            "stop-out": self.stopouts,
            "otv. L/S": f"{nl}/{len(self.pos) - nl}",
        }


def run(bars, strat, name: str) -> Acct:
    """Spoločná slučka: swap → TP/SL v sviečke → stop-out → stratégia."""
    a = Acct(name)
    for i, (t, o, h, l, c) in enumerate(bars):
        a.rollover(t)
        for p in [p for p in a.pos if p.bar < i]:
            if p.side == "long":
                if p.sl is not None and l <= p.sl:
                    a.close(p, p.sl)
                elif p.tp is not None and h >= p.tp:
                    a.close(p, p.tp)
                    strat.on_tp(p, a)
            else:
                if p.sl is not None and h + SPREAD >= p.sl:
                    a.close(p, p.sl)
                elif p.tp is not None and l + SPREAD <= p.tp:
                    a.close(p, p.tp)
                    strat.on_tp(p, a)
        # stop-out na horšom konci sviečky pre väčšiu stranu
        nl = a.n("long")
        a.stop_out(l if nl >= len(a.pos) - nl else h)
        strat.on_bar(i, t, o, h, l, c, a)
        a.mark(c)
    return a


# --- indikátory ---------------------------------------------------------------

class Ema:
    def __init__(self, n: int):
        self.k = 2 / (n + 1)
        self.v: float | None = None

    def add(self, x: float) -> float:
        self.v = x if self.v is None else self.v + self.k * (x - self.v)
        return self.v


class H1:
    """H1 sviečky z M5 + ATR(14) a EMA na H1 zatváracích cenách."""

    def __init__(self, fast: int, slow: int):
        self.cur = None
        self.fast, self.slow = Ema(fast), Ema(slow)
        self.atr = None
        self.prev_c = None
        self.n = 0

    def add(self, t, o, h, l, c) -> bool:
        k = int(t // 3600)
        closed = False
        if self.cur is None or self.cur[0] != k:
            if self.cur is not None:
                _, co, ch, cl, cc = self.cur
                pc = self.prev_c if self.prev_c is not None else co
                tr = max(ch - cl, abs(ch - pc), abs(cl - pc))
                self.atr = tr if self.atr is None else self.atr * 13 / 14 + tr / 14
                self.prev_c = cc
                self.fast.add(cc)
                self.slow.add(cc)
                self.n += 1
                closed = True
            self.cur = [k, o, h, l, c]
        else:
            self.cur[2] = max(self.cur[2], h)
            self.cur[3] = min(self.cur[3], l)
            self.cur[4] = c
        return closed


# --- stratégie ---------------------------------------------------------------

class G2B:
    """Dnešný živý grid (Grid25-G2B-CT): kotva = posledný vstup, kroky
    0,15 % / 0,10 %, TP 0,1 %, pásmo 1,12–1,16, strop 30, bez SL."""

    def __init__(self, step_l=0.0015, step_s=0.0010, band=(1.12, 1.16),
                 anchor="extreme"):
        self.anchor = anchor
        self.sl_, self.ss = step_l, step_s
        self.band = band
        self.ref_l = self.ref_s = None
        self.last_l = self.last_s = 0.0
        self.atr = None
        self.pc = None

    def on_tp(self, p, a):
        if a.n(p.side) == 0:
            if p.side == "long":
                self.ref_l = p.tp
            else:
                self.ref_s = p.tp

    def on_bar(self, i, t, o, h, l, c, a):
        pc = self.pc if self.pc is not None else o
        tr = max(h - l, abs(h - pc), abs(l - pc))
        self.atr = tr if self.atr is None else self.atr * 13 / 14 + tr / 14
        self.pc = c
        if self.anchor == "extreme":
            self.ref_l = max(self.ref_l or c, h)
            self.ref_s = min(self.ref_s or c, l)
        else:
            self.ref_l = c if self.ref_l is None else self.ref_l
            self.ref_s = c if self.ref_s is None else self.ref_s
        nl, ns = a.n("long"), a.n("short")
        if c < self.band[1] and nl < 30:
            drop = self.ref_l - c
            unlock = nl < 20 or abs(c - self.last_l) > 2 * self.atr
            if drop >= max(self.ref_l * self.sl_, 2 * self.atr) and unlock:
                gap = int(drop / (self.ref_l * self.sl_)) >= 2
                tp = round(c * (1 + self.sl_) if gap else c * 1.001, 5)
                a.open("long", c, tp, None, i)
                self.last_l = self.ref_l = c + SPREAD
        if c > self.band[0] and ns < 30:
            rise = c - self.ref_s
            unlock = ns < 20 or abs(c - self.last_s) > 2 * self.atr
            if rise >= self.ref_s * self.ss and unlock:
                gap = int(rise / (self.ref_s * self.ss)) >= 2
                tp = round(c * (1 - self.ss) if gap else c * 0.999, 5)
                a.open("short", c, tp, None, i)
                self.last_s = self.ref_s = c


class TrendGrid:
    """Grid len v smere trendu (H1 EMA fast/slow s hysterézou).

    Uptrend: nákupy na poklesoch o krok od posledného vstupu (prázdna strana
    sleduje maximum), TP tp_pct. Downtrend zrkadlovo. Proti trendu nič.
    Pri obrate trendu sa pozície starej strany buď nechajú dobehnúť na TP
    (flip="keep"), alebo sa zatvoria (flip="close"). Strop `cap` na stranu
    a voliteľný košíkový stop: keď float strany klesne pod −basket € (alebo
    −basket_pct % kapitálu), strana sa zatvorí."""

    def __init__(self, fast=50, slow=200, step=0.0015, tp=0.001, cap=15,
                 flip="keep", basket=None, hyst=0.0, atr_step=None):
        self.h1 = H1(fast, slow)
        self.step, self.tp, self.cap = step, tp, cap
        self.flip, self.basket, self.hyst = flip, basket, hyst
        self.atr_step = atr_step
        self.trend = 0
        self.ref = None

    def on_tp(self, p, a):
        if a.n(p.side) == 0:
            self.ref = None

    def on_bar(self, i, t, o, h, l, c, a):
        if self.h1.add(t, o, h, l, c) and self.h1.n > 200:
            f, s = self.h1.fast.v, self.h1.slow.v
            d = (f - s) / s
            new = 1 if d > self.hyst else -1 if d < -self.hyst else self.trend
            if new != self.trend:
                old = "long" if self.trend > 0 else "short"
                self.trend = new
                self.ref = None
                if self.flip == "close":
                    for p in [p for p in a.pos if p.side == old]:
                        a.close(p, a.exit_price(p, c))
        if self.basket is not None:
            for side in ("long", "short"):
                fl = sum((c - p.entry) * QTY / c if p.side == "long"
                         else (p.entry - c - SPREAD) * QTY / c
                         for p in a.pos if p.side == side)
                if fl < -self.basket:
                    for p in [p for p in a.pos if p.side == side]:
                        a.close(p, a.exit_price(p, c))
                    if (side == "long") == (self.trend > 0):
                        self.ref = None
        if self.trend == 0:
            return
        side = "long" if self.trend > 0 else "short"
        n = a.n(side)
        step = self.step
        if self.atr_step and self.h1.atr:
            step = max(step, self.atr_step * self.h1.atr / c)
        if n == 0 or self.ref is None:
            # prázdna strana sleduje extrém: vstup na prvom poklese o krok
            self.ref = max(self.ref or c, h) if side == "long" else min(self.ref or c, l)
        if n >= self.cap:
            return
        if side == "long" and self.ref - c >= self.ref * step:
            a.open("long", c, round(c * (1 + self.tp), 5), None, i)
            self.ref = c + SPREAD
        elif side == "short" and c - self.ref >= self.ref * step:
            a.open("short", c, round(c * (1 - self.tp), 5), None, i)
            self.ref = c


class GuardedGrid:
    """Hustý obojstranný grid ako G2B (kotva = bežiaci extrém), ale s tromi
    poistkami, ktoré dnešnému gridu chýbajú:

    1. trend (H1 EMA fast/slow): strana proti trendu smie mať najviac
       `cap_ct` pozícií, strana v smere trendu `cap` — grid neprikupuje
       donekonečna proti pohybu, ktorý ho potápa,
    2. strážca marže: pod `guard` % zatvorí najstratovejšiu pozíciu skôr,
       než to urobí broker pri 50 %,
    3. pásmo je voliteľné — trendový filter je jeho dynamická náhrada.
    """

    def __init__(self, step_l=0.0015, step_s=0.0015, tp=0.001, cap=20, cap_ct=6,
                 fast=50, slow=200, guard=None, band=None, unlock_atr=True,
                 step_ct=None):
        self.step_ct = step_ct
        self.step = {"long": step_l, "short": step_s}
        self.tp, self.cap, self.cap_ct = tp, cap, cap_ct
        self.h1 = H1(fast, slow)
        self.guard, self.band = guard, band
        self.ref = {"long": None, "short": None}
        self.last = {"long": 0.0, "short": 0.0}
        self.atr = None
        self.pc = None
        self.unlock_atr = unlock_atr

    def on_tp(self, p, a):
        pass

    def trend(self) -> int:
        if self.h1.n < 200:
            return 0
        return 1 if self.h1.fast.v > self.h1.slow.v else -1

    def on_bar(self, i, t, o, h, l, c, a):
        self.h1.add(t, o, h, l, c)
        pc = self.pc if self.pc is not None else o
        tr = max(h - l, abs(h - pc), abs(l - pc))
        self.atr = tr if self.atr is None else self.atr * 13 / 14 + tr / 14
        self.pc = c
        if self.guard is not None:
            while a.pos:
                used = a.used()
                if a.equity(c) / used * 100 >= self.guard:
                    break
                worst = min(a.pos, key=lambda p: (c - p.entry) if p.side == "long"
                            else (p.entry - c - SPREAD))
                a.close(worst, a.exit_price(worst, c))
        self.ref["long"] = max(self.ref["long"] or c, h)
        self.ref["short"] = min(self.ref["short"] or c, l)
        tr_ = self.trend()
        for side in ("long", "short"):
            n = a.n(side)
            with_trend = (tr_ >= 0) if side == "long" else (tr_ <= 0)
            cap = self.cap if with_trend else self.cap_ct
            if n >= cap:
                continue
            if self.band and ((side == "long" and c >= self.band[1]) or
                              (side == "short" and c <= self.band[0])):
                continue
            ref, st = self.ref[side], self.step[side]
            if self.step_ct is not None and not with_trend:
                st = self.step_ct
            move = ref - c if side == "long" else c - ref
            need = ref * st
            if side == "long":
                need = max(need, 2 * self.atr)
            unlock = (not self.unlock_atr) or n < 20 or abs(c - self.last[side]) > 2 * self.atr
            if move >= need and unlock:
                gap = int(move / (ref * st)) >= 2
                if side == "long":
                    tp = round(c * (1 + st) if gap else c * (1 + self.tp), 5)
                    a.open("long", c, tp, None, i)
                    self.last["long"] = self.ref["long"] = c + SPREAD
                else:
                    tp = round(c * (1 - st) if gap else c * (1 - self.tp), 5)
                    a.open("short", c, tp, None, i)
                    self.last["short"] = self.ref["short"] = c


class Real:
    """Spustí skutočnú triedu stratégie z trading/ v lab engine — overenie,
    že bot a lab počítajú to isté."""

    def __init__(self, strat):
        from trading.strategy_base import Bar
        self.Bar = Bar
        self.s = strat
        self.atr = None
        self.pc = None
        self.ids = 0

    def on_tp(self, p, a):
        self.s.on_trade_closed(p.tag, p.side, p.tp)

    def on_bar(self, i, t, o, h, l, c, a):
        pc = self.pc if self.pc is not None else o
        tr = max(h - l, abs(h - pc), abs(l - pc))
        self.atr = tr if self.atr is None else self.atr * 13 / 14 + tr / 14
        self.pc = c
        for sig in self.s.on_bar(self.Bar(t, o, h, l, c), self.atr):
            self.ids += 1
            a.open(sig.side, c, sig.tp_price, None, i, tag=self.ids)
            self.s.on_trade_opened(self.ids, sig.side, a.pos[-1].entry)


def table(rows: list[dict], title: str) -> None:
    print(f"\n=== {title} ===")
    keys = list(rows[0])
    w = {k: max(len(k), *(len(str(r[k])) for r in rows)) for k in keys}
    print("  ".join(k.ljust(w[k]) for k in keys))
    for r in rows:
        print("  ".join(str(r[k]).ljust(w[k]) for k in keys))


B = (1.12, 1.16)
B = (1.12, 1.16)
B = (1.12, 1.16)


def _guard():
    from trading.strategy_guard import GuardGrid
    return Real(GuardGrid())


CANDIDATES = {
    "G2B live (extrém)": lambda: G2B(),
    "GG 20/4 .10 ct.20 100/400": lambda: GuardedGrid(
        band=B, step_l=0.0010, step_s=0.0010, step_ct=0.0020, cap_ct=4,
        fast=100, slow=400),
    "Strážca (trading/strategy_guard.py)": _guard,
}


def main() -> int:
    bars = load()
    live = [b for b in bars if b[0] >= LIVE_START]
    mid = datetime(2025, 10, 15, tzinfo=timezone.utc).timestamp()
    y1 = [b for b in bars if b[0] < mid]
    y2 = [b for b in bars if b[0] >= mid]
    only = sys.argv[1:]
    for title, data in (("2 roky (10/2024–10/2026)", bars),
                        ("1. rok (10/2024–10/2025)", y1),
                        ("2. rok (10/2025–10/2026)", y2),
                        ("live obdobie (od 6. 8. 2026)", live)):
        rows = []
        for name, mk in CANDIDATES.items():
            if only and not any(o in name for o in only):
                continue
            rows.append(run(data, mk(), name).row())
        table(rows, title)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
