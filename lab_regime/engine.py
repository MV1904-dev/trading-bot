#!/usr/bin/env python3
"""Backtest engine pre režimový test G2B.

Reprodukuje mechaniku trading/strategy_grid25.py na denných baroch:
* ref_long beží ako max(high), ref_short ako min(low); po vstupe sa kotva
  presunie na vstupnú cenu, po vyprázdnení strany na cenu zatvorenia
* long vstup pri poklese ≥ max(krok, atr_mult × ATR) od ref_long
* short vstup pri raste ≥ krok od ref_short (bez ATR podmienky — tak to
  má aj live kód, asymetria je zámerná)
* gap ≥ 2 úrovne → TP na preskočenú úroveň namiesto tp_pct
* kapacita base + reserve na smer, rezervné úrovne len ďalej než 2 × ATR
* max 1 vstup na smer a bar

Rozdiely oproti live kódu, ktoré vyplývajú z denných dát:
* vstup sa vykoná na zatvorení baru (live: rovnako, on_bar dostáva
  uzavretý bar)
* TP sa môže vyplniť najskôr NASLEDUJÚCI bar — bar, v ktorom sa vstúpilo,
  je už uzavretý. Konzervatívne.
* keď bar prekročí TP aj v opačnom smere, počíta sa TP (grid nemá SL,
  takže druhá strana nič nespúšťa)

Bez look-ahead: ATR aj medián sa počítajú z barov PRED aktuálnym, sadzby
sa berú s posunom o jeden deň (hodnota známa ráno v čase t).
"""

from __future__ import annotations

import bisect
import csv
from dataclasses import dataclass, field, replace
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"

# Náklady podľa nameraných hodnôt na účte (31. 7. 2026):
#   provízia 0,90 € za 10k round trip, spread 0,10 pipu
COMMISSION_PER_UNIT = 0.90 / 10_000        # € za jednotku, round trip
SPREAD_PRICE = 0.00001                      # 0,1 pipu, platí sa raz (vstup)

# Swap: diferenciál ECB − Fed mínus prirážka brokera. Prirážka 1,4 % p.a.
# je kalibrovaná na dnešok — dá long −2,78 % p.a. (= 0,76 €/10k/noc, čo
# sedí s pozorovaním) a short ≈ 0 (pozorované −0,105 pipu). Kladná strana
# sa zaokrúhľuje na nulu: broker priaznivú stranu nevypláca, čo potvrdzuje
# aj to, že GBPUSD má obe strany záporné.
SWAP_MARKUP_PA = 1.4


@dataclass
class Cfg:
    qty: float = 10_000.0
    step_short: float = 0.0015
    step_long: float = 0.00225
    tp_pct: float = 0.001
    band_low: float = 1.1200
    band_high: float = 1.1600
    atr_mult: float = 2.0
    base_levels: int = 20
    reserve_levels: int = 10
    atr_period: int = 14
    # --- varianty ---
    v1_atr_step: bool = False       # krok = k × ATR(20) / cena
    v1_k: float = 1.0
    v1_atr_period: int = 20
    v2_median_anchor: bool = False  # pásma relatívne k 200-dňovému mediánu
    v2_median_win: int = 200
    v2_band_rel: float = 0.0175     # ±1,75 % ≈ šírka pôvodných 1,12–1,16
    v3_rate_gate: bool = False      # longy len ak diferenciál nie je proti nim
    no_bands: bool = False          # kontrolný beh bez pásiem

    @property
    def cap(self) -> int:
        return self.base_levels + self.reserve_levels


@dataclass
class Pos:
    side: str
    entry: float
    tp: float
    opened: int          # index baru
    swap: float = 0.0


@dataclass
class Result:
    equity: list[tuple[str, float]] = field(default_factory=list)
    closed: list[dict] = field(default_factory=list)
    max_open: int = 0
    full_ladder_bars: int = 0
    bars: int = 0
    max_float_pct: float = 0.0
    blocked_by_gate: int = 0


def load_prices() -> list[tuple[str, float, float, float, float]]:
    out = []
    with (DATA / "eurusd_d.csv").open() as f:
        for r in csv.DictReader(f):
            out.append((r["date"], float(r["open"]), float(r["high"]),
                        float(r["low"]), float(r["close"])))
    return out


def load_h1() -> list[tuple[str, float, float, float, float]]:
    """H1 dataset 2013–2026. G2B sa na D1 testovať nedá — viď build_h1.py."""
    out = []
    with (DATA / "eurusd_h1.csv").open() as f:
        for r in csv.DictReader(f):
            out.append((r["date"], float(r["open"]), float(r["high"]),
                        float(r["low"]), float(r["close"])))
    return out


def _load_rate(name: str) -> tuple[list[str], list[float]]:
    ds, vs = [], []
    with (DATA / name).open() as f:
        for r in csv.DictReader(f):
            try:
                v = float(r["rate"])
            except ValueError:
                continue
            ds.append(r["date"])
            vs.append(v)
    return ds, vs


class Rates:
    """Sadzby s posunom o jeden deň — hodnota známa v čase t, nie zajtrajšia."""

    def __init__(self) -> None:
        self.ed, self.ev = _load_rate("ecb_dfr.csv")
        self.fd, self.fv = _load_rate("fed_dff.csv")

    @staticmethod
    def _at(ds: list[str], vs: list[float], d: str) -> float | None:
        i = bisect.bisect_left(ds, d) - 1      # striktne PRED dňom d
        return vs[i] if i >= 0 else None

    def diff(self, d: str) -> float | None:
        """ECB − Fed v % p.a. Kladné = long EUR je úrokovo v prospech."""
        e = self._at(self.ed, self.ev, d)
        f = self._at(self.fd, self.fv, d)
        return None if e is None or f is None else e - f

    def swap_pa(self, d: str) -> tuple[float, float]:
        """(long, short) v % p.a., vždy ≤ 0."""
        diff = self.diff(d)
        if diff is None:
            return -SWAP_MARKUP_PA, -SWAP_MARKUP_PA
        return (min(diff - SWAP_MARKUP_PA, 0.0),
                min(-diff - SWAP_MARKUP_PA, 0.0))


def _atr(bars: list, i: int, period: int) -> float | None:
    """ATR z barov PRED indexom i (žiadny look-ahead)."""
    if i < period + 1:
        return None
    trs = []
    for j in range(i - period, i):
        h, l, pc = bars[j][2], bars[j][3], bars[j - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def run(bars: list, cfg: Cfg, rates: Rates,
        start: int = 0, end: int | None = None,
        cfg_at=None) -> Result:
    """cfg_at(i) → Cfg umožní meniť parametre počas behu bez toho, aby sa
    zahodili otvorené pozície. Sekanie behu na ročné okná by gridu vzalo
    jeho jadro — držať podvodné úrovne, kým sa cena nevráti."""
    end = end if end is not None else len(bars)
    res = Result()
    longs: list[Pos] = []
    shorts: list[Pos] = []
    ref_long = ref_short = None
    last_long = last_short = 0.0
    realized = 0.0
    last_swap_day = ""
    closes = [b[4] for b in bars]

    for i in range(start, end):
        if cfg_at is not None:
            cfg = cfg_at(i)
        d, _o, hi, lo, c = bars[i]
        res.bars += 1

        # --- swap za držanie cez noc -----------------------------------
        # Účtuje sa RAZ ZA DEŇ pri rollovere, nie na každom bare. Na H1
        # by inak vyšla sadzba 24× vyššia (35 % p.a. namiesto 2,8 %).
        day = d[:10]
        if day != last_swap_day:
            last_swap_day = day
            sl_pa, ss_pa = rates.swap_pa(d)
            for p in longs:
                p.swap += cfg.qty * (sl_pa / 100.0) / 365.0
            for p in shorts:
                p.swap += cfg.qty * (ss_pa / 100.0) / 365.0

        # --- výplň TP (najskôr bar po vstupe) --------------------------
        for book, is_long in ((longs, True), (shorts, False)):
            for p in list(book):
                if p.opened >= i:
                    continue
                filled = (hi >= p.tp) if is_long else (lo <= p.tp)
                if not filled:
                    continue
                gross = ((p.tp - p.entry) if is_long else (p.entry - p.tp)) * cfg.qty
                cost = cfg.qty * COMMISSION_PER_UNIT + SPREAD_PRICE * cfg.qty
                net = gross - cost + p.swap
                realized += net
                res.closed.append({
                    "date": d, "side": "long" if is_long else "short",
                    "entry": p.entry, "exit": p.tp, "gross": gross,
                    "cost": cost, "swap": p.swap, "net": net,
                    "held": i - p.opened,
                })
                book.remove(p)

        # --- kotvy -----------------------------------------------------
        ref_long = max(ref_long if ref_long is not None else c, hi)
        ref_short = min(ref_short if ref_short is not None else c, lo)

        atr = _atr(bars, i, cfg.atr_period)
        if atr is None:
            res.equity.append((d, realized))
            continue

        # --- krok: pevný alebo volatilitný (V1) -------------------------
        if cfg.v1_atr_step:
            a20 = _atr(bars, i, cfg.v1_atr_period)
            if a20 is None:
                res.equity.append((d, realized))
                continue
            step_l = step_s = cfg.v1_k * a20 / c
        else:
            step_l, step_s = cfg.step_long, cfg.step_short

        # --- pásma: absolútne, relatívne k mediánu (V2), alebo žiadne ---
        if cfg.no_bands:
            allow_long = allow_short = True
        elif cfg.v2_median_anchor:
            w = cfg.v2_median_win
            if i < w:
                res.equity.append((d, realized))
                continue
            win = sorted(closes[i - w:i])
            med = win[len(win) // 2]
            allow_long = c < med * (1 + cfg.v2_band_rel)
            allow_short = c > med * (1 - cfg.v2_band_rel)
        else:
            allow_long = c < cfg.band_high
            allow_short = c > cfg.band_low

        # --- V3: sadzbový gate ------------------------------------------
        if cfg.v3_rate_gate:
            diff = rates.diff(d)
            if diff is not None and diff < 0 and allow_long:
                allow_long = False
                res.blocked_by_gate += 1

        # --- vstupy ------------------------------------------------------
        if allow_long and len(longs) < cfg.cap:
            drop = ref_long - c
            trigger = max(ref_long * step_l, cfg.atr_mult * atr)
            unlock = (len(longs) < cfg.base_levels
                      or abs(c - last_long) > cfg.atr_mult * atr)
            if drop >= trigger and unlock:
                k = int(drop / (ref_long * step_l)) if step_l > 0 else 1
                tp = c * (1 + step_l) if k >= 2 else c * (1 + cfg.tp_pct)
                longs.append(Pos("long", c, tp, i))
                last_long = c
                ref_long = c

        if allow_short and len(shorts) < cfg.cap:
            rise = c - ref_short
            unlock = (len(shorts) < cfg.base_levels
                      or abs(c - last_short) > cfg.atr_mult * atr)
            if rise >= ref_short * step_s and unlock:
                k = int(rise / (ref_short * step_s)) if step_s > 0 else 1
                tp = c * (1 - step_s) if k >= 2 else c * (1 - cfg.tp_pct)
                shorts.append(Pos("short", c, tp, i))
                last_short = c
                ref_short = c

        if not longs:
            ref_long = c
        if not shorts:
            ref_short = c

        # --- metriky -----------------------------------------------------
        n = len(longs) + len(shorts)
        res.max_open = max(res.max_open, n)
        if len(longs) >= cfg.cap or len(shorts) >= cfg.cap:
            res.full_ladder_bars += 1
        floating = (sum((c - p.entry) * cfg.qty + p.swap for p in longs)
                    + sum((p.entry - c) * cfg.qty + p.swap for p in shorts))
        res.max_float_pct = min(res.max_float_pct, floating)
        res.equity.append((d, realized + floating))

    return res
