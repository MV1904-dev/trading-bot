"""STRATEGY LAB — systematické vyhodnotenie grid a SL scenárov na EURUSD.

Metodika
--------
* Dáta: IBKR M5 (2023-08 → 2026-07) + Dukascopy H1 (2013 → 2023).
* Delenie: in-sample (IS) 2023–2024, out-of-sample (OOS) 2025–2026,
  stres 2014–2015 a 2021–2022 (H1).
* Denné indikátory (MA200, ATR-percentily, 3-ročné percentily cien) sa
  počítajú z dennej série zreťazenej Dukascopy (2013+) + IBKR, pričom deň D
  používa výhradne dáta ≤ D−1 → žiadny look-ahead. Pre stres 2014–15 je
  warmup len ~1 rok (expandujúce okno) — poznamenané.
* Náklady: provízia 0.2 bps min $2/príkaz, spread 0.1 pipu round-trip
  (midpoint dáta), funding z tabuľky Fed−ECB (trading/rates.py).
* Rebríček: pomer P/L / max floating DD na OOS.

Kill kritériá
-------------
* COST_FAIL   náklady / hrubý zisk > 25 % (OOS)
* OVERFIT     anualizovaný OOS P/L < 50 % anualizovaného IS P/L
* FRAGILE     kľúčový parameter ±20 % (OOS) → P/L padne o > 60 %
* STRESS_FATAL min. kapitál 1:30 v niektorom stres období > 100 000 €

Interpretačné rozhodnutia (zámerné, viď zadanie)
------------------------------------------------
* S1–S4: matica „TP 0.2 %/0.3 % × 25k/50k“ nahrádza štrukturálne TP
  scenárov; S1/S2 navyše prah impulzu {2×, 3×} ATR.
* S4 nemá historický kalendár (feed dáva len aktuálny týždeň) → „news
  spike“ = bar s range > 4× ATR(14); vstup proti smeru 15–30 min po ňom.
* Impulzné okno „15 min“ = 3 bary na M5, 1 bar na H1.
* Scalpy držia max 1 pozíciu naraz.

Beh: python3 strategy_lab.py   (progres na stdout; CSV sa dopĺňa priebežne)
Výstup: data/backtest_v2/results_lab.csv + top 10 tabuľka na stdout.
"""

from __future__ import annotations

import csv
import sys
import time as _time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from backtest_v2 import (Bars, atr_wilder, load_dukascopy_h1, load_ibkr_csv,
                         slice_years, IBKR_CSV)
from trading.rates import daily_funding_usd

ROOT = Path(__file__).resolve().parent
OUT_CSV = ROOT / "data" / "backtest_v2" / "results_lab.csv"

# --- náklady ----------------------------------------------------------------
COMM_BPS = 0.2e-4
COMM_MIN = 2.0
HALF_SPREAD = 0.000005
LOCAL_TZ = ZoneInfo("Europe/Bratislava")

STRESS_FATAL_CAP = 100_000.0     # min. kapitál 1:30 v strese nad túto hranicu


# ===========================================================================
# Denný kontext (bez look-ahead: hodnoty pre deň D sú z dát <= D-1)
# ===========================================================================

@dataclass
class DailyCtx:
    days: list[str]                 # zoradené 'YYYY-MM-DD'
    ma200: np.ndarray               # NaN, kým nie je 200 dní histórie
    atr_d_rank: np.ndarray          # percentil včerajšieho denného ATR (0-100)
    q03: np.ndarray                 # 3-ročné cenové kvantily (trailing 756 d)
    q10: np.ndarray
    q90: np.ndarray
    q97: np.ndarray
    lo3y: np.ndarray
    hi3y: np.ndarray
    index: dict                     # day -> i


def build_daily_ctx(duka: Bars, ibkr: Bars) -> DailyCtx:
    """Zreťazí denné OHLC z Dukascopy (2013+) a IBKR a spočíta trailing
    indikátory. Pri prekryve (2023) má prednosť IBKR."""
    daily: dict[str, list] = {}     # day -> [o, h, l, c]
    for bars in (duka, ibkr):
        for i in range(len(bars)):
            d = datetime.fromtimestamp(int(bars.t[i]), tz=timezone.utc).strftime("%Y-%m-%d")
            rec = daily.get(d)
            if rec is None or bars is ibkr and rec[4] != "ibkr":
                if rec is None or rec[4] != "ibkr":
                    daily[d] = [bars.open[i], bars.high[i], bars.low[i],
                                bars.close[i], "ibkr" if bars is ibkr else "duka"]
                    continue
            rec[1] = max(rec[1], bars.high[i])
            rec[2] = min(rec[2], bars.low[i])
            rec[3] = bars.close[i]

    days = sorted(daily)
    o = np.array([daily[d][0] for d in days])
    h = np.array([daily[d][1] for d in days])
    l = np.array([daily[d][2] for d in days])
    c = np.array([daily[d][3] for d in days])
    n = len(days)

    # denné ATR(14) Wilder
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr_d = np.full(n, np.nan)
    if n > 14:
        atr_d[13] = tr[:14].mean()
        for i in range(14, n):
            atr_d[i] = atr_d[i - 1] * 13 / 14 + tr[i] / 14

    ma200 = np.full(n, np.nan)
    atr_rank = np.full(n, 50.0)
    q03 = np.full(n, np.nan); q10 = np.full(n, np.nan)
    q90 = np.full(n, np.nan); q97 = np.full(n, np.nan)
    lo3 = np.full(n, np.nan); hi3 = np.full(n, np.nan)

    csum = np.concatenate(([0.0], np.cumsum(c)))
    for i in range(1, n):
        j = i - 1                    # dáta len po deň i-1
        if j >= 199:
            ma200[i] = (csum[j + 1] - csum[j - 199]) / 200
        if j >= 14:
            hist = atr_d[13:j + 1]
            hist = hist[~np.isnan(hist)]
            if len(hist) > 20:
                atr_rank[i] = 100.0 * (hist < atr_d[j]).mean()
        w0 = max(0, j - 755)
        win = c[w0:j + 1]
        if len(win) >= 250:
            q03[i], q10[i], q90[i], q97[i] = np.percentile(win, [3, 10, 90, 97])
            lo3[i], hi3[i] = win.min(), win.max()

    return DailyCtx(days, ma200, atr_rank, q03, q10, q90, q97, lo3, hi3,
                    {d: i for i, d in enumerate(days)})


@dataclass
class Prepared:
    """Dataset s predpočítanými poľami pre rýchly beh."""
    name: str
    bars: Bars
    atr: np.ndarray
    day_i: np.ndarray               # index do DailyCtx
    hour_local: np.ndarray
    years: float
    impulse_window: int             # 3 na M5 (15 min), 1 na H1


def prepare(name: str, bars: Bars, ctx: DailyCtx, impulse_window: int) -> Prepared:
    day_i = np.empty(len(bars), dtype=np.int64)
    hour = np.empty(len(bars), dtype=np.int64)
    for i in range(len(bars)):
        dt = datetime.fromtimestamp(int(bars.t[i]), tz=timezone.utc)
        day_i[i] = ctx.index.get(dt.strftime("%Y-%m-%d"), -1)
        hour[i] = dt.astimezone(LOCAL_TZ).hour
    years = (bars.t[-1] - bars.t[0]) / (365.25 * 86400)
    return Prepared(name, bars, atr_wilder(bars), day_i, hour, max(years, 1e-9),
                    impulse_window)


# ===========================================================================
# Metriky
# ===========================================================================

@dataclass
class Metrics:
    pnl: float = 0.0                # EUR, realizované + funding + floating konca
    gross_win: float = 0.0          # hrubý zisk zavretých obchodov pred nákladmi
    costs: float = 0.0              # provízie + spread + záporný funding
    commissions: float = 0.0
    spread: float = 0.0
    funding: float = 0.0
    trades: int = 0
    wins: int = 0
    max_dd: float = 0.0
    min_cap: float = 0.0
    max_expo: float = 0.0
    underwater_days: float = 0.0    # najdlhšie obdobie pod vodou (dni)
    open_end: int = 0
    failsafe_days: int = 0          # G8: dni s aktívnou režimovou poistkou

    @property
    def cost_ratio(self) -> float:
        return self.costs / self.gross_win if self.gross_win > 0 else 99.0


class Tracker:
    """Equity/DD/kapitál/underwater tracking spoločný pre oba enginy."""

    def __init__(self):
        self.realized = 0.0          # EUR (po nákladoch, s fundingom)
        self.m = Metrics()
        self.peak = 0.0
        self.peak_t: float | None = None

    def fill_cost(self, qty: float, price: float) -> float:
        c_usd = max(COMM_MIN, qty * price * COMM_BPS)
        s_usd = qty * HALF_SPREAD
        self.m.commissions += c_usd / price
        self.m.spread += s_usd / price
        return (c_usd + s_usd) / price

    def step(self, t: float, equity: float, expo: float) -> None:
        m = self.m
        m.max_expo = max(m.max_expo, expo)
        if self.peak_t is None or equity > self.peak:
            if self.peak_t is not None:
                m.underwater_days = max(m.underwater_days,
                                        (t - self.peak_t) / 86400)
            self.peak = equity
            self.peak_t = t
        m.max_dd = max(m.max_dd, self.peak - equity)
        m.min_cap = max(m.min_cap, expo / 30.0 - equity)

    def finish(self, t_end: float, float_eur: float, open_n: int) -> Metrics:
        m = self.m
        if self.peak_t is not None:
            m.underwater_days = max(m.underwater_days,
                                    (t_end - self.peak_t) / 86400)
        m.pnl = self.realized + float_eur
        m.open_end = open_n
        m.costs = m.commissions + m.spread + max(-m.funding, 0.0)
        m.min_cap = max(m.min_cap, 0.0)
        return m


# ===========================================================================
# GRID ENGINE (G1–G8)
# ===========================================================================

@dataclass
class GridCfg:
    qty: float = 25_000
    step_s: float = 0.0015
    step_l_ratio: float = 1.5
    tp: float = 0.001
    cap_base: int = 20
    cap_reserve: int = 10
    atr_mult_long: float = 2.0
    bands: str = "fixed"            # fixed | none | ma200
    band_lo: float = 1.1200
    band_hi: float = 1.1600
    ma_band_pct: float = 0.02
    gap_mode: str = "none"          # none | double_vol | tp_skipped
    session: tuple | None = None    # (9, 18) lokálne hodiny; None = 24 h
    regime_atr_pause: bool = False  # denná ATR > 80. percentil → bez vstupov
    short_qty_mult: float = 1.0     # G4
    percentile_scaling: bool = False  # G8

    @property
    def cap(self) -> int:
        return self.cap_base + self.cap_reserve


def run_grid(p: Prepared, dctx: DailyCtx, cfg: GridCfg) -> Metrics:
    b, atr = p.bars, p.atr
    tr = Tracker()
    step_l = cfg.step_s * cfg.step_l_ratio
    longs: list[tuple] = []          # (entry, qty, tp)
    shorts: list[tuple] = []
    ref_l = ref_s = b.close[0]
    last_l = last_s = 0.0
    prev_day = -1
    failsafe_prev = False

    for i in range(len(b.t)):
        t, h, l, c = b.t[i], b.high[i], b.low[i], b.close[i]
        di = p.day_i[i]

        # funding pri zmene dňa
        if di != prev_day and prev_day >= 0 and di >= 0:
            day = dctx.days[di]
            nd = max(di - prev_day, 1) if p.name.startswith("IBKR") else 1
            for e, q, _tp in longs:
                tr.m.funding += daily_funding_usd(day, "long", q, c) * nd / c
            for e, q, _tp in shorts:
                tr.m.funding += daily_funding_usd(day, "short", q, c) * nd / c
        if di >= 0:
            prev_day = di

        # TP výstupy
        if longs:
            keep = []
            for e, q, tp in longs:
                if h >= tp:
                    gross = (tp - e) * q / tp
                    tr.m.gross_win += max(gross, 0.0)
                    tr.realized += gross - tr.fill_cost(q, tp)
                    tr.m.trades += 1
                    tr.m.wins += 1
                else:
                    keep.append((e, q, tp))
            longs = keep
            if not longs:
                ref_l = c
        if shorts:
            keep = []
            for e, q, tp in shorts:
                if l <= tp:
                    gross = (e - tp) * q / tp
                    tr.m.gross_win += max(gross, 0.0)
                    tr.realized += gross - tr.fill_cost(q, tp)
                    tr.m.trades += 1
                    tr.m.wins += 1
                else:
                    keep.append((e, q, tp))
            shorts = keep
            if not shorts:
                ref_s = c

        ref_l = max(ref_l, h)
        ref_s = min(ref_s, l)

        # --- vstupné filtre ------------------------------------------------
        a = atr[i]
        can_enter = not np.isnan(a)
        if can_enter and cfg.session is not None:
            can_enter = cfg.session[0] <= p.hour_local[i] < cfg.session[1]
        if can_enter and cfg.regime_atr_pause and di >= 0:
            can_enter = dctx.atr_d_rank[di] <= 80.0

        allow_long = allow_short = True
        if cfg.bands == "fixed":
            allow_long, allow_short = c < cfg.band_hi, c > cfg.band_lo
        elif cfg.bands == "ma200" and di >= 0 and not np.isnan(dctx.ma200[di]):
            lo = dctx.ma200[di] * (1 - cfg.ma_band_pct)
            hi = dctx.ma200[di] * (1 + cfg.ma_band_pct)
            allow_long, allow_short = c < hi, c > lo

        # G8 percentilové škálovanie kapacity + režimová poistka
        cap_l = cap_s = cfg.cap
        if cfg.percentile_scaling and di >= 0 and not np.isnan(dctx.q90[di]):
            failsafe = (c > dctx.hi3y[di] * 1.02) or (c < dctx.lo3y[di] * 0.98)
            if failsafe:
                cap_l = cap_s = int(cfg.cap * 0.5)
                if not failsafe_prev:
                    tr.m.failsafe_days += 1
            else:
                if c > dctx.q97[di]:
                    cap_s = int(cfg.cap * 2.0)
                elif c > dctx.q90[di]:
                    cap_s = int(cfg.cap * 1.5)
                if c < dctx.q03[di]:
                    cap_l = int(cfg.cap * 2.0)
                elif c < dctx.q10[di]:
                    cap_l = int(cfg.cap * 1.5)
            failsafe_prev = failsafe

        if can_enter:
            # long
            if allow_long and len(longs) < cap_l:
                drop = ref_l - c
                trigger = max(ref_l * step_l, cfg.atr_mult_long * a)
                unlock = (len(longs) < cfg.cap_base
                          or abs(c - last_l) > 2.0 * a)
                if drop >= trigger and unlock:
                    k = int(drop / (ref_l * step_l)) if step_l > 0 else 1
                    q = cfg.qty * (2.0 if cfg.gap_mode == "double_vol" and k >= 2 else 1.0)
                    tp = c * (1 + step_l) if (cfg.gap_mode == "tp_skipped" and k >= 2) \
                        else c * (1 + cfg.tp)
                    tr.realized -= tr.fill_cost(q, c)
                    longs.append((c, q, tp))
                    last_l = c
                    ref_l = c
            # short
            if allow_short and len(shorts) < cap_s:
                rise = c - ref_s
                s_step = cfg.step_s / (cfg.short_qty_mult if cfg.short_qty_mult > 1 else 1.0)
                unlock = (len(shorts) < cfg.cap_base
                          or abs(c - last_s) > 2.0 * a)
                if rise >= ref_s * s_step and unlock:
                    k = int(rise / (ref_s * s_step))
                    q = cfg.qty * cfg.short_qty_mult
                    if cfg.gap_mode == "double_vol" and k >= 2:
                        q *= 2.0
                    tp = c * (1 - s_step) if (cfg.gap_mode == "tp_skipped" and k >= 2) \
                        else c * (1 - cfg.tp)
                    tr.realized -= tr.fill_cost(q, c)
                    shorts.append((c, q, tp))
                    last_s = c
                    ref_s = c

        float_eur = (sum((c - e) * q for e, q, _ in longs)
                     + sum((e - c) * q for e, q, _ in shorts)) / c
        expo = sum(q for _, q, _ in longs) + sum(q for _, q, _ in shorts)
        tr.step(t, tr.realized + float_eur, expo)

    c_end = b.close[-1]
    float_eur = (sum((c_end - e) * q for e, q, _ in longs)
                 + sum((e - c_end) * q for e, q, _ in shorts)) / c_end
    return tr.finish(b.t[-1], float_eur, len(longs) + len(shorts))


# ===========================================================================
# SCALP ENGINE (S1–S4)
# ===========================================================================

@dataclass
class ScalpCfg:
    mode: str = "S1"                # S1 | S2 | S3 | S4
    qty: float = 25_000
    tp_pct: float = 0.002
    thr_mult: float = 2.0           # S1/S2 prah impulzu (×ATR)
    sl_mult: float = 1.0            # S1/S2: 1×ATR, S3: 1.5×ATR
    spike_mult: float = 4.0         # S4 detekcia news spiku


def run_scalp(p: Prepared, dctx: DailyCtx, cfg: ScalpCfg) -> Metrics:
    b, atr, w = p.bars, p.atr, p.impulse_window
    tr = Tracker()
    pos = None                       # (side, entry, qty, sl, tp)
    prev_day = -1
    pending: tuple | None = None     # S2/S4 čakajúci setup
    n = len(b.t)

    for i in range(n):
        t, h, l, c, o = b.t[i], b.high[i], b.low[i], b.close[i], b.open[i]
        di = p.day_i[i]
        if di != prev_day and prev_day >= 0 and di >= 0 and pos is not None:
            day = dctx.days[di]
            tr.m.funding += daily_funding_usd(day, pos[0], pos[2], c) / c
        if di >= 0:
            prev_day = di

        # --- exit (SL má prednosť — konzervatívne) -------------------------
        if pos is not None:
            side, e, q, sl, tp = pos
            exit_p = None
            win = False
            if side == "long":
                if l <= sl:
                    exit_p = sl
                elif h >= tp:
                    exit_p, win = tp, True
            else:
                if h >= sl:
                    exit_p = sl
                elif l <= tp:
                    exit_p, win = tp, True
            if exit_p is not None:
                gross = (exit_p - e) * q / exit_p if side == "long" \
                    else (e - exit_p) * q / exit_p
                tr.m.gross_win += max(gross, 0.0)
                tr.realized += gross - tr.fill_cost(q, exit_p)
                tr.m.trades += 1
                tr.m.wins += int(win)
                pos = None

        a = atr[i]
        if pos is None and not np.isnan(a) and i > w:
            move = c - b.close[i - w]

            if cfg.mode == "S1":
                if abs(move) >= cfg.thr_mult * a:
                    side = "long" if move > 0 else "short"
                    sl = c - cfg.sl_mult * a if side == "long" else c + cfg.sl_mult * a
                    tp = c * (1 + cfg.tp_pct) if side == "long" else c * (1 - cfg.tp_pct)
                    tr.realized -= tr.fill_cost(cfg.qty, c)
                    pos = (side, c, cfg.qty, sl, tp)

            elif cfg.mode == "S2":
                if pending is None and abs(move) >= cfg.thr_mult * a:
                    pending = ("long" if move > 0 else "short", c, i, abs(move))
                elif pending is not None:
                    side, c0, i0, imp = pending
                    if i - i0 > 3:
                        pending = None
                    else:
                        retr = (c0 - c) if side == "long" else (c - c0)
                        if retr >= 0.38 * imp:
                            sl = c - cfg.sl_mult * a if side == "long" else c + cfg.sl_mult * a
                            tp = c * (1 + cfg.tp_pct) if side == "long" else c * (1 - cfg.tp_pct)
                            tr.realized -= tr.fill_cost(cfg.qty, c)
                            pos = (side, c, cfg.qty, sl, tp)
                            pending = None

            elif cfg.mode == "S3":
                if abs(move) >= 3.0 * a:
                    side = "short" if move > 0 else "long"   # proti pohybu
                    sl = c + cfg.sl_mult * a if side == "short" else c - cfg.sl_mult * a
                    tp = c * (1 - cfg.tp_pct) if side == "short" else c * (1 + cfg.tp_pct)
                    tr.realized -= tr.fill_cost(cfg.qty, c)
                    pos = (side, c, cfg.qty, sl, tp)

            elif cfg.mode == "S4":
                rng = h - l
                if pending is None and rng >= cfg.spike_mult * a:
                    pending = ("short" if c > o else "long", h if c > o else l, i, None)
                elif pending is not None:
                    side, extreme, i0, _ = pending
                    wait = 3 if w == 3 else 1        # 15 min na M5, 1 bar na H1
                    late = 6 if w == 3 else 2
                    if i - i0 > late:
                        pending = None
                    elif i - i0 >= wait:
                        sl = extreme * (1.0005 if side == "short" else 0.9995)
                        tp = c * (1 - cfg.tp_pct) if side == "short" else c * (1 + cfg.tp_pct)
                        tr.realized -= tr.fill_cost(cfg.qty, c)
                        pos = (side, c, cfg.qty, sl, tp)
                        pending = None

        if pos is not None:
            side, e, q, sl, tp = pos
            float_eur = ((c - e) * q if side == "long" else (e - c) * q) / c
            expo = q
        else:
            float_eur = expo = 0.0
        tr.step(t, tr.realized + float_eur, expo)

    c_end = b.close[-1]
    if pos is not None:
        side, e, q, sl, tp = pos
        float_eur = ((c_end - e) * q if side == "long" else (e - c_end) * q) / c_end
    else:
        float_eur = 0.0
    return tr.finish(b.t[-1], float_eur, 0 if pos is None else 1)


# ===========================================================================
# Scenáre
# ===========================================================================

def build_scenarios() -> list[tuple]:
    """[(scenario, variant_id, family, cfg, kľúčový_parameter)]"""
    out = []
    base = GridCfg()

    out.append(("G1", "G1_baseline", "grid", base, "step_s"))

    out.append(("G2", "G2A_gap2x", "grid",
                replace(base, gap_mode="double_vol"), "step_s"))
    out.append(("G2", "G2B_gap_tp_skip", "grid",
                replace(base, gap_mode="tp_skipped"), "step_s"))
    # G2C = baseline (referencia je G1)

    for tp in (0.0010, 0.0015):
        for st in (0.0010, 0.0015, 0.0020, 0.0025):
            for cap in ((20, 0), (20, 10)):
                vid = (f"G3_tp{tp * 1e4:.0f}bp_st{st * 1e4:.0f}bp_"
                       f"cap{cap[0] + cap[1]}")
                out.append(("G3", vid, "grid",
                            replace(base, tp=tp, step_s=st,
                                    cap_base=cap[0], cap_reserve=cap[1]),
                            "step_s"))

    out.append(("G4", "G4a_asym_agresiv", "grid",
                replace(base, short_qty_mult=1.5, atr_mult_long=3.0,
                        step_l_ratio=2.0), "step_s"))
    out.append(("G4", "G4b_asym_mierny", "grid",
                replace(base, short_qty_mult=1.25, atr_mult_long=2.5),
                "step_s"))

    out.append(("G5", "G5_likvidne_9_18", "grid",
                replace(base, session=(9, 18)), "step_s"))
    out.append(("G6", "G6_atr_regime", "grid",
                replace(base, regime_atr_pause=True), "step_s"))
    out.append(("G7", "G7_ma200_pasma", "grid",
                replace(base, bands="ma200"), "step_s"))
    out.append(("G8", "G8_percentile", "grid",
                replace(base, percentile_scaling=True), "step_s"))

    for thr in (2.0, 3.0):
        for tp in (0.002, 0.003):
            for q in (25_000, 50_000):
                out.append(("S1", f"S1_thr{thr:.0f}_tp{tp * 1e3:.0f}_q{q // 1000}k",
                            "scalp", ScalpCfg("S1", q, tp, thr, 1.0), "sl_mult"))
                out.append(("S2", f"S2_thr{thr:.0f}_tp{tp * 1e3:.0f}_q{q // 1000}k",
                            "scalp", ScalpCfg("S2", q, tp, thr, 1.0), "sl_mult"))
    for tp in (0.002, 0.003):
        for q in (25_000, 50_000):
            out.append(("S3", f"S3_tp{tp * 1e3:.0f}_q{q // 1000}k",
                        "scalp", ScalpCfg("S3", q, tp, 3.0, 1.5), "sl_mult"))
            out.append(("S4", f"S4_tp{tp * 1e3:.0f}_q{q // 1000}k",
                        "scalp", ScalpCfg("S4", q, tp, 3.0, 1.0), "sl_mult"))
    return out


def run_cfg(p: Prepared, dctx: DailyCtx, family: str, cfg) -> Metrics:
    return run_grid(p, dctx, cfg) if family == "grid" else run_scalp(p, dctx, cfg)


def shift_param(cfg, key: str, mult: float):
    return replace(cfg, **{key: getattr(cfg, key) * mult})


# ===========================================================================
# Main
# ===========================================================================

CSV_COLS = [
    "scenario", "variant", "family",
    "pnl_is", "pnl_oos", "pnl_s14", "pnl_s22",
    "dd_is", "dd_oos", "dd_s14", "dd_s22",
    "ratio_oos", "min_cap_oos", "min_cap_s14", "min_cap_s22",
    "cost_ratio_oos", "trades_is", "trades_oos", "win_oos_pct",
    "underwater_days_oos", "max_expo_oos", "funding_oos",
    "pnl_oos_p08", "pnl_oos_p12",
    "flags", "failsafe_days",
]


def main() -> int:
    t0 = _time.time()
    print("STRATEGY LAB — načítavam dáta…", flush=True)
    ibkr = load_ibkr_csv(IBKR_CSV)
    duka = load_dukascopy_h1()
    dctx = build_daily_ctx(duka, ibkr)

    def years_slice(bars, name, y0, y1):
        return slice_years(bars, name, y0, y1)

    print("Pripravujem datasety…", flush=True)
    ds_is = prepare("IBKR_IS_2023-24", years_slice(ibkr, "IS", 2023, 2024), dctx, 3)
    ds_oos = prepare("IBKR_OOS_2025-26", years_slice(ibkr, "OOS", 2025, 2026), dctx, 3)
    ds_s14 = prepare("STRES_2014-15", years_slice(duka, "S14", 2014, 2015), dctx, 1)
    ds_s22 = prepare("STRES_2021-22", years_slice(duka, "S22", 2021, 2022), dctx, 1)
    for d in (ds_is, ds_oos, ds_s14, ds_s22):
        print(f"  {d.name}: {len(d.bars):,} barov, {d.years:.2f} r.", flush=True)

    scenarios = build_scenarios()
    print(f"Scenárov/variantov: {len(scenarios)}; behy: "
          f"{len(scenarios) * 4} + fragilita.", flush=True)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fcsv = open(OUT_CSV, "w", newline="")
    writer = csv.writer(fcsv)
    writer.writerow(CSV_COLS)

    rows = []
    for k, (scen, vid, family, cfg, key_param) in enumerate(scenarios, 1):
        m_is = run_cfg(ds_is, dctx, family, cfg)
        m_oos = run_cfg(ds_oos, dctx, family, cfg)
        m_s14 = run_cfg(ds_s14, dctx, family, cfg)
        m_s22 = run_cfg(ds_s22, dctx, family, cfg)

        # fragilita: kľúčový parameter ±20 % na OOS (len ak OOS > 0)
        p08 = p12 = float("nan")
        if m_oos.pnl > 0:
            p08 = run_cfg(ds_oos, dctx, family,
                          shift_param(cfg, key_param, 0.8)).pnl
            p12 = run_cfg(ds_oos, dctx, family,
                          shift_param(cfg, key_param, 1.2)).pnl

        flags = []
        if m_oos.cost_ratio > 0.25:
            flags.append("COST_FAIL")
        is_ann = m_is.pnl / ds_is.years
        oos_ann = m_oos.pnl / ds_oos.years
        if m_is.pnl > 0 and oos_ann < 0.5 * is_ann:
            flags.append("OVERFIT")
        if m_is.pnl <= 0:
            flags.append("IS_NEGATIVE")
        if m_oos.pnl > 0 and not np.isnan(p08):
            if min(p08, p12) < 0.4 * m_oos.pnl:
                flags.append("FRAGILE")
        if max(m_s14.min_cap, m_s22.min_cap) > STRESS_FATAL_CAP:
            flags.append("STRESS_FATAL")

        ratio = m_oos.pnl / m_oos.max_dd if m_oos.max_dd > 0 else 0.0
        row = [scen, vid, family,
               round(m_is.pnl), round(m_oos.pnl),
               round(m_s14.pnl), round(m_s22.pnl),
               round(m_is.max_dd), round(m_oos.max_dd),
               round(m_s14.max_dd), round(m_s22.max_dd),
               round(ratio, 3), round(m_oos.min_cap),
               round(m_s14.min_cap), round(m_s22.min_cap),
               round(m_oos.cost_ratio, 3), m_is.trades, m_oos.trades,
               round(100 * m_oos.wins / m_oos.trades, 1) if m_oos.trades else 0,
               round(m_oos.underwater_days, 1), round(m_oos.max_expo),
               round(m_oos.funding),
               round(p08) if not np.isnan(p08) else "",
               round(p12) if not np.isnan(p12) else "",
               "|".join(flags), m_oos.failsafe_days]
        writer.writerow(row)
        fcsv.flush()
        rows.append((ratio, row))
        el = _time.time() - t0
        print(f"[{k}/{len(scenarios)}] {vid:<28} "
              f"IS {m_is.pnl:>9.0f}  OOS {m_oos.pnl:>9.0f}  "
              f"S14 {m_s14.pnl:>9.0f}  S22 {m_s22.pnl:>9.0f}  "
              f"ratio {ratio:>6.2f}  [{','.join(flags) or 'ok'}]  "
              f"({el:.0f}s)", flush=True)

    fcsv.close()

    print("\n=== TOP 10 podľa P/L / maxDD (OOS) ===", flush=True)
    hdr = (f"{'variant':<28}{'OOS P/L':>9}{'OOS DD':>8}{'ratio':>7}"
           f"{'IS P/L':>9}{'S14':>9}{'S22':>9}{'cost%':>7}{'kap':>8}  flagy")
    print(hdr)
    print("-" * len(hdr))
    for ratio, r in sorted(rows, key=lambda x: -x[0])[:10]:
        print(f"{r[1]:<28}{r[4]:>9}{r[8]:>8}{ratio:>7.2f}"
              f"{r[3]:>9}{r[5]:>9}{r[6]:>9}{100 * r[15]:>6.1f}%"
              f"{r[12]:>8}  {r[24]}")
    print(f"\nCSV: {OUT_CSV.relative_to(ROOT)}  "
          f"(celkový čas {(_time.time() - t0) / 60:.1f} min)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
