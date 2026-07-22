"""STRATEGY LAB — Oanda vetva: SL rodina (S1–S4) s Oanda nákladovým modelom.

Pointa vetvy: malý kapitál. Oanda core pricing nemá minimum provízie
(3.5 USD / 100k / strana), takže skalpy s pozíciami 5k/10k/25k nie sú
vopred mŕtve ako na IBKR (min $2/príkaz). Zato je tu POVINNÝ slippage —
pri stratégiách so SL 1–1.5× ATR(M5) je rozhodujúcim nákladom.

Nákladový model
---------------
* provízia: 3.5 USD / 100 000 jednotiek / strana (bez minima)
* spread: 0.15 pipu bežne; 0.5 pipu v rollover okne 21:00–23:00 UTC
  (posledná + prvá hodina FX dňa) a na baroch s range > 4× ATR(14)
  (proxy za ±30 min okolo high-impact správ — historický kalendár nie je
  k dispozícii). Polovica spreadu sa účtuje na každý fill.
* slippage: základ 0.2 pipu na stranu, citlivosť {0, 0.2, 0.5}; účtuje sa
  na market fily (vstup) a stop fily (SL) — TP limitka sa plní presne.
* funding: tabuľka Fed−ECB (trading/rates.py) — pri skalpoch zanedbateľný,
  ale účtuje sa.

Metodika = hlavný lab: IS 2023–24, OOS 2025–26, stresy 2014–15/2021–22,
kill pravidlá (COST_FAIL > 25 %, OVERFIT < 50 % IS, FRAGILE ±20 % sl_mult).
Navyše: min. kapitál pri 1:30, max séria strát, najhorší deň a týždeň,
a „death slip“ — slippage, pri ktorom OOS P/L pretne nulu.

Beh: python3 strategy_lab_oanda.py
Výstup: data/backtest_v2/results_lab_oanda.csv + top 5 na stdout.
"""

from __future__ import annotations

import csv
import sys
import time as _time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import strategy_lab as sl
from backtest_v2 import load_dukascopy_h1, load_ibkr_csv, slice_years, IBKR_CSV
from trading.rates import daily_funding_usd

ROOT = Path(__file__).resolve().parent
OUT_CSV = ROOT / "data" / "backtest_v2" / "results_lab_oanda.csv"

# --- Oanda náklady -----------------------------------------------------------
COMM_PER_100K_SIDE = 3.5
SPREAD_NORMAL = 0.15e-4
SPREAD_WIDE = 0.5e-4
SLIP_BASE = 0.2e-4
SLIP_LEVELS = (0.0, 0.2e-4, 0.5e-4)
NEWS_RANGE_MULT = 4.0          # bar range > 4×ATR = news proxy → široký spread
ROLLOVER_UTC = (21, 22)        # 21:00–23:00 UTC

STRESS_FATAL_CAP = 100_000.0


@dataclass
class OCfg:
    mode: str                   # S1 | S2 | S3 | S4
    qty: float
    tp_pct: float
    thr_mult: float = 2.0
    sl_mult: float = 1.0
    slip: float = SLIP_BASE

    @property
    def vid(self) -> str:
        base = f"{self.mode}_tp{self.tp_pct * 1e3:.0f}_q{self.qty / 1000:.0f}k"
        if self.mode in ("S1", "S2"):
            base = f"{self.mode}_thr{self.thr_mult:.0f}_" + base.split("_", 1)[1]
        return base


@dataclass
class OMetrics:
    pnl: float = 0.0
    gross_win: float = 0.0
    costs: float = 0.0
    trades: int = 0
    wins: int = 0
    max_dd: float = 0.0
    min_cap: float = 0.0
    underwater_days: float = 0.0
    max_loss_streak: int = 0
    worst_day: float = 0.0
    worst_week: float = 0.0

    @property
    def cost_ratio(self) -> float:
        return self.costs / self.gross_win if self.gross_win > 0 else 99.0

    @property
    def win_rate(self) -> float:
        return 100.0 * self.wins / self.trades if self.trades else 0.0


def run_scalp_oanda(p: sl.Prepared, dctx: sl.DailyCtx, cfg: OCfg) -> OMetrics:
    b, atr, w = p.bars, p.atr, p.impulse_window
    m = OMetrics()
    realized = 0.0                 # EUR po nákladoch
    comm = spread_c = slip_c = 0.0
    funding = 0.0
    pos = None                     # (side, entry, sl, tp)
    pending = None
    prev_day = -1
    peak, peak_t = 0.0, None
    streak = 0
    daily: dict[str, float] = {}
    n = len(b.t)

    def fill_cost(price: float, *, market: bool, wide: bool) -> float:
        """EUR náklad jedného fillu (provízia + pol spreadu + príp. slippage)."""
        nonlocal comm, spread_c, slip_c
        c_usd = cfg.qty / 100_000 * COMM_PER_100K_SIDE
        s_usd = cfg.qty * (SPREAD_WIDE if wide else SPREAD_NORMAL) / 2
        sl_usd = cfg.qty * cfg.slip if market else 0.0
        comm += c_usd / price
        spread_c += s_usd / price
        slip_c += sl_usd / price
        return (c_usd + s_usd + sl_usd) / price

    for i in range(n):
        t, o, h, l, c = b.t[i], b.open[i], b.high[i], b.low[i], b.close[i]
        a = atr[i]
        di = p.day_i[i]
        hour_utc = int(t // 3600) % 24
        wide = hour_utc in ROLLOVER_UTC or \
            (not np.isnan(a) and (h - l) >= NEWS_RANGE_MULT * a)
        dkey = dctx.days[di] if di >= 0 else datetime.fromtimestamp(
            int(t), tz=timezone.utc).strftime("%Y-%m-%d")

        if di != prev_day and prev_day >= 0 and di >= 0 and pos is not None:
            funding += daily_funding_usd(dkey, pos[0], cfg.qty, c) / c
        if di >= 0:
            prev_day = di

        # --- exit: SL má prednosť (konzervatívne) --------------------------
        if pos is not None:
            side, e, sl_p, tp_p = pos
            exit_p = win = None
            if side == "long":
                if l <= sl_p:
                    exit_p, win, market = sl_p, False, True
                elif h >= tp_p:
                    exit_p, win, market = tp_p, True, False
            else:
                if h >= sl_p:
                    exit_p, win, market = sl_p, False, True
                elif l <= tp_p:
                    exit_p, win, market = tp_p, True, False
            if exit_p is not None:
                gross = (exit_p - e) * cfg.qty / exit_p if side == "long" \
                    else (e - exit_p) * cfg.qty / exit_p
                m.gross_win += max(gross, 0.0)
                net = gross - fill_cost(exit_p, market=market, wide=wide)
                realized += net
                m.trades += 1
                daily[dkey] = daily.get(dkey, 0.0) + net
                if win:
                    m.wins += 1
                    streak = 0
                else:
                    streak += 1
                    m.max_loss_streak = max(m.max_loss_streak, streak)
                pos = None

        # --- vstupy ---------------------------------------------------------
        if pos is None and not np.isnan(a) and i > w:
            move = c - b.close[i - w]
            if cfg.mode == "S1":
                if abs(move) >= cfg.thr_mult * a:
                    side = "long" if move > 0 else "short"
                    sl_p = c - cfg.sl_mult * a if side == "long" else c + cfg.sl_mult * a
                    tp_p = c * (1 + cfg.tp_pct) if side == "long" else c * (1 - cfg.tp_pct)
                    realized -= fill_cost(c, market=True, wide=wide)
                    pos = (side, c, sl_p, tp_p)
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
                            sl_p = c - cfg.sl_mult * a if side == "long" else c + cfg.sl_mult * a
                            tp_p = c * (1 + cfg.tp_pct) if side == "long" else c * (1 - cfg.tp_pct)
                            realized -= fill_cost(c, market=True, wide=wide)
                            pos = (side, c, sl_p, tp_p)
                            pending = None
            elif cfg.mode == "S3":
                if abs(move) >= 3.0 * a:
                    side = "short" if move > 0 else "long"
                    sl_p = c + cfg.sl_mult * a if side == "short" else c - cfg.sl_mult * a
                    tp_p = c * (1 - cfg.tp_pct) if side == "short" else c * (1 + cfg.tp_pct)
                    realized -= fill_cost(c, market=True, wide=wide)
                    pos = (side, c, sl_p, tp_p)
            elif cfg.mode == "S4":
                rng = h - l
                if pending is None and rng >= NEWS_RANGE_MULT * a:
                    pending = ("short" if c > o else "long", h if c > o else l, i, None)
                elif pending is not None:
                    side, extreme, i0, _ = pending
                    wait = 3 if w == 3 else 1
                    late = 6 if w == 3 else 2
                    if i - i0 > late:
                        pending = None
                    elif i - i0 >= wait:
                        sl_p = extreme * (1.0005 if side == "short" else 0.9995)
                        tp_p = c * (1 - cfg.tp_pct) if side == "short" else c * (1 + cfg.tp_pct)
                        realized -= fill_cost(c, market=True, wide=wide)
                        pos = (side, c, sl_p, tp_p)
                        pending = None

        # --- equity tracking -------------------------------------------------
        if pos is not None:
            side, e, _sl, _tp = pos
            float_eur = ((c - e) if side == "long" else (e - c)) * cfg.qty / c
            expo = cfg.qty
        else:
            float_eur = expo = 0.0
        eq = realized + funding + float_eur
        if peak_t is None or eq > peak:
            if peak_t is not None:
                m.underwater_days = max(m.underwater_days, (t - peak_t) / 86400)
            peak, peak_t = eq, t
        m.max_dd = max(m.max_dd, peak - eq)
        m.min_cap = max(m.min_cap, expo / 30.0 - eq)

    if peak_t is not None:
        m.underwater_days = max(m.underwater_days, (b.t[-1] - peak_t) / 86400)

    c_end = b.close[-1]
    if pos is not None:
        side, e, _sl, _tp = pos
        realized += ((c_end - e) if side == "long" else (e - c_end)) * cfg.qty / c_end
    m.pnl = realized + funding
    m.costs = comm + spread_c + slip_c + max(-funding, 0.0)
    m.min_cap = max(m.min_cap, 0.0)

    # najhorší deň / ISO týždeň (z realizovaných čistých P/L)
    if daily:
        m.worst_day = min(daily.values())
        weekly: dict[str, float] = {}
        for d, v in daily.items():
            y, wk, _ = datetime.strptime(d, "%Y-%m-%d").isocalendar()
            weekly[f"{y}-W{wk:02d}"] = weekly.get(f"{y}-W{wk:02d}", 0.0) + v
        m.worst_week = min(weekly.values())
    return m


def build_variants() -> list[OCfg]:
    out = []
    for thr in (2.0, 3.0):
        for tp in (0.002, 0.003):
            for q in (5_000, 10_000, 25_000):
                out.append(OCfg("S1", q, tp, thr, 1.0))
                out.append(OCfg("S2", q, tp, thr, 1.0))
    for tp in (0.002, 0.003):
        for q in (5_000, 10_000, 25_000):
            out.append(OCfg("S3", q, tp, 3.0, 1.5))
            out.append(OCfg("S4", q, tp, 3.0, 1.0))
    return out


def death_slip(p0: float, p02: float, p05: float) -> str:
    """Interpolovaný slippage (pipy), pri ktorom OOS P/L pretne nulu."""
    pts = [(0.0, p0), (0.2, p02), (0.5, p05)]
    if pts[0][1] <= 0:
        return "0 (mŕtve aj bez slippage)"
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if y1 > 0 >= y2:
            return f"{x1 + (x2 - x1) * y1 / (y1 - y2):.2f}"
    return ">0.5"


# ===========================================================================
# GRID rodina s Oanda nákladmi (--grid): shortlist overených konfigurácií
# z hlavného labu × malé pozície 2k/5k/10k. Slippage na market vstupy,
# TP limitky sa plnia presne; spread 0.15/0.5 pipu podľa času/news proxy.
# ===========================================================================

@dataclass
class GMetrics:
    pnl: float = 0.0
    gross_win: float = 0.0
    costs: float = 0.0
    cycles: int = 0
    opened: int = 0
    open_end: int = 0
    max_dd: float = 0.0
    min_cap: float = 0.0
    max_expo: float = 0.0
    underwater_days: float = 0.0
    worst_day: float = 0.0
    worst_week: float = 0.0
    funding: float = 0.0

    @property
    def cost_ratio(self) -> float:
        return self.costs / self.gross_win if self.gross_win > 0 else 99.0


def run_grid_oanda(p: sl.Prepared, dctx: sl.DailyCtx, cfg,
                   slip: float = SLIP_BASE) -> GMetrics:
    b, atr = p.bars, p.atr
    m = GMetrics()
    realized = comm = spread_c = slip_c = funding = 0.0
    step_l = cfg.step_s * cfg.step_l_ratio
    longs: list[tuple] = []          # (entry, qty, tp)
    shorts: list[tuple] = []
    ref_l = ref_s = b.close[0]
    last_l = last_s = 0.0
    prev_day = -1
    failsafe_prev = False
    peak, peak_t = 0.0, None
    daily: dict[str, float] = {}

    def fill_cost(qty: float, price: float, *, market: bool, wide: bool) -> float:
        nonlocal comm, spread_c, slip_c
        c_usd = qty / 100_000 * COMM_PER_100K_SIDE
        s_usd = qty * (SPREAD_WIDE if wide else SPREAD_NORMAL) / 2
        sl_usd = qty * slip if market else 0.0
        comm += c_usd / price
        spread_c += s_usd / price
        slip_c += sl_usd / price
        return (c_usd + s_usd + sl_usd) / price

    for i in range(len(b.t)):
        t, h, l, c = b.t[i], b.high[i], b.low[i], b.close[i]
        a = atr[i]
        di = p.day_i[i]
        hour_utc = int(t // 3600) % 24
        wide = hour_utc in ROLLOVER_UTC or \
            (not np.isnan(a) and (h - l) >= NEWS_RANGE_MULT * a)
        dkey = dctx.days[di] if di >= 0 else None

        if di != prev_day and prev_day >= 0 and di >= 0:
            nd = max(di - prev_day, 1) if p.name in ("IS", "OOS") else 1
            for e, q, _tp in longs:
                funding += daily_funding_usd(dkey, "long", q, c) * nd / c
            for e, q, _tp in shorts:
                funding += daily_funding_usd(dkey, "short", q, c) * nd / c
        if di >= 0:
            prev_day = di

        # TP výstupy (limitky — bez slippage)
        if longs:
            keep = []
            for e, q, tp in longs:
                if h >= tp:
                    gross = (tp - e) * q / tp
                    m.gross_win += max(gross, 0.0)
                    net = gross - fill_cost(q, tp, market=False, wide=wide)
                    realized += net
                    m.cycles += 1
                    if dkey:
                        daily[dkey] = daily.get(dkey, 0.0) + net
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
                    m.gross_win += max(gross, 0.0)
                    net = gross - fill_cost(q, tp, market=False, wide=wide)
                    realized += net
                    m.cycles += 1
                    if dkey:
                        daily[dkey] = daily.get(dkey, 0.0) + net
                else:
                    keep.append((e, q, tp))
            shorts = keep
            if not shorts:
                ref_s = c

        ref_l = max(ref_l, h)
        ref_s = min(ref_s, l)

        can_enter = not np.isnan(a)
        if can_enter and cfg.session is not None:
            can_enter = cfg.session[0] <= p.hour_local[i] < cfg.session[1]
        if can_enter and cfg.regime_atr_pause and di >= 0:
            can_enter = dctx.atr_d_rank[di] <= 80.0

        allow_long = allow_short = True
        if cfg.bands == "fixed":
            allow_long, allow_short = c < cfg.band_hi, c > cfg.band_lo

        cap_l = cap_s = cfg.cap
        if cfg.percentile_scaling and di >= 0 and not np.isnan(dctx.q90[di]):
            failsafe = (c > dctx.hi3y[di] * 1.02) or (c < dctx.lo3y[di] * 0.98)
            if failsafe:
                cap_l = cap_s = int(cfg.cap * 0.5)
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
                    realized -= fill_cost(q, c, market=True, wide=wide)
                    longs.append((c, q, tp))
                    m.opened += 1
                    last_l = c
                    ref_l = c
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
                    realized -= fill_cost(q, c, market=True, wide=wide)
                    shorts.append((c, q, tp))
                    m.opened += 1
                    last_s = c
                    ref_s = c

        float_eur = (sum((c - e) * q for e, q, _ in longs)
                     + sum((e - c) * q for e, q, _ in shorts)) / c
        expo = sum(q for _, q, _ in longs) + sum(q for _, q, _ in shorts)
        eq = realized + funding + float_eur
        m.max_expo = max(m.max_expo, expo)
        if peak_t is None or eq > peak:
            if peak_t is not None:
                m.underwater_days = max(m.underwater_days, (t - peak_t) / 86400)
            peak, peak_t = eq, t
        m.max_dd = max(m.max_dd, peak - eq)
        m.min_cap = max(m.min_cap, expo / 30.0 - eq)

    if peak_t is not None:
        m.underwater_days = max(m.underwater_days, (b.t[-1] - peak_t) / 86400)
    c_end = b.close[-1]
    float_eur = (sum((c_end - e) * q for e, q, _ in longs)
                 + sum((e - c_end) * q for e, q, _ in shorts)) / c_end
    m.pnl = realized + funding + float_eur
    m.open_end = len(longs) + len(shorts)
    m.funding = funding
    m.costs = comm + spread_c + slip_c + max(-funding, 0.0)
    m.min_cap = max(m.min_cap, 0.0)
    if daily:
        m.worst_day = min(daily.values())
        weekly: dict[str, float] = {}
        for d, v in daily.items():
            y, wk, _ = datetime.strptime(d, "%Y-%m-%d").isocalendar()
            weekly[f"{y}-W{wk:02d}"] = weekly.get(f"{y}-W{wk:02d}", 0.0) + v
        m.worst_week = min(weekly.values())
    return m


GRID_SHORTLIST = [
    ("G1_baseline", {}),
    ("G2A_gap2x", {"gap_mode": "double_vol"}),
    ("G2B_gap_tp_skip", {"gap_mode": "tp_skipped"}),
    ("G3_cap20", {"cap_base": 20, "cap_reserve": 0}),
    ("G4b_asym_mierny", {"short_qty_mult": 1.25, "atr_mult_long": 2.5}),
    ("G5_likvidne_9_18", {"session": (9, 18)}),
    ("G6_atr_regime", {"regime_atr_pause": True}),
    ("G8_percentile", {"percentile_scaling": True}),
]

GRID_CSV_COLS = [
    "scenario", "variant", "qty",
    "pnl_is", "pnl_oos", "pnl_s14", "pnl_s22",
    "dd_oos", "ratio_oos", "pnl_oos_slip0", "pnl_oos_slip05", "death_slip_pips",
    "cost_ratio_oos", "cycles_oos", "min_cap_oos", "min_cap_s14", "min_cap_s22",
    "max_expo_oos", "worst_day_oos", "worst_week_oos", "underwater_days_oos",
    "funding_oos", "pnl_oos_p08", "pnl_oos_p12", "flags",
]


def grid_main() -> int:
    t0 = _time.time()
    print("OANDA GRID LAB — načítavam dáta…", flush=True)
    ibkr = load_ibkr_csv(IBKR_CSV)
    duka = load_dukascopy_h1()
    dctx = sl.build_daily_ctx(duka, ibkr)
    ds_is = sl.prepare("IS", slice_years(ibkr, "IS", 2023, 2024), dctx, 3)
    ds_oos = sl.prepare("OOS", slice_years(ibkr, "OOS", 2025, 2026), dctx, 3)
    ds_s14 = sl.prepare("S14", slice_years(duka, "S14", 2014, 2015), dctx, 1)
    ds_s22 = sl.prepare("S22", slice_years(duka, "S22", 2021, 2022), dctx, 1)

    out_csv = ROOT / "data" / "backtest_v2" / "results_lab_oanda_grid.csv"
    f = open(out_csv, "w", newline="")
    wr = csv.writer(f)
    wr.writerow(GRID_CSV_COLS)

    variants = [(name, kw, q) for name, kw in GRID_SHORTLIST
                for q in (2_000, 5_000, 10_000)]
    print(f"Variantov: {len(variants)}", flush=True)

    rows = []
    for k, (name, kw, q) in enumerate(variants, 1):
        cfg = sl.GridCfg(qty=q, **kw)
        vid = f"{name}_q{q // 1000}k"
        m_is = run_grid_oanda(ds_is, dctx, cfg)
        m_oos = run_grid_oanda(ds_oos, dctx, cfg)
        m_s14 = run_grid_oanda(ds_s14, dctx, cfg)
        m_s22 = run_grid_oanda(ds_s22, dctx, cfg)
        m_o0 = run_grid_oanda(ds_oos, dctx, cfg, slip=SLIP_LEVELS[0])
        m_o5 = run_grid_oanda(ds_oos, dctx, cfg, slip=SLIP_LEVELS[2])

        p08 = p12 = float("nan")
        if m_oos.pnl > 0:
            p08 = run_grid_oanda(ds_oos, dctx,
                                 replace(cfg, step_s=cfg.step_s * 0.8)).pnl
            p12 = run_grid_oanda(ds_oos, dctx,
                                 replace(cfg, step_s=cfg.step_s * 1.2)).pnl

        flags = []
        if m_oos.cost_ratio > 0.25:
            flags.append("COST_FAIL")
        if m_is.pnl > 0 and m_oos.pnl / ds_oos.years < 0.5 * m_is.pnl / ds_is.years:
            flags.append("OVERFIT")
        if m_is.pnl <= 0:
            flags.append("IS_NEGATIVE")
        if m_oos.pnl > 0 and not np.isnan(p08) and \
                min(p08, p12) < 0.4 * m_oos.pnl:
            flags.append("FRAGILE")
        if max(m_s14.min_cap, m_s22.min_cap) > STRESS_FATAL_CAP:
            flags.append("STRESS_FATAL")

        ratio = m_oos.pnl / m_oos.max_dd if m_oos.max_dd > 0 else 0.0
        ds_str = death_slip(m_o0.pnl, m_oos.pnl, m_o5.pnl)
        row = [name, vid, q,
               round(m_is.pnl), round(m_oos.pnl), round(m_s14.pnl),
               round(m_s22.pnl), round(m_oos.max_dd), round(ratio, 3),
               round(m_o0.pnl), round(m_o5.pnl), ds_str,
               round(m_oos.cost_ratio, 3), m_oos.cycles,
               round(m_oos.min_cap), round(m_s14.min_cap),
               round(m_s22.min_cap), round(m_oos.max_expo),
               round(m_oos.worst_day), round(m_oos.worst_week),
               round(m_oos.underwater_days, 1), round(m_oos.funding),
               round(p08) if not np.isnan(p08) else "",
               round(p12) if not np.isnan(p12) else "",
               "|".join(flags)]
        wr.writerow(row)
        f.flush()
        rows.append((ratio, row))
        print(f"[{k}/{len(variants)}] {vid:<24} IS {m_is.pnl:>7.0f}  "
              f"OOS {m_oos.pnl:>7.0f}  S14 {m_s14.pnl:>7.0f}  "
              f"S22 {m_s22.pnl:>7.0f}  cost {100 * m_oos.cost_ratio:>4.1f}%  "
              f"kap {m_oos.min_cap:>6.0f}  death {ds_str:<6} "
              f"[{','.join(flags) or 'ok'}]  ({_time.time() - t0:.0f}s)",
              flush=True)
    f.close()

    print("\n=== TOP 10 podľa P/L / maxDD (OOS, slip 0.2) ===")
    hdr = (f"{'variant':<24}{'OOS':>8}{'DD':>7}{'ratio':>7}{'IS':>7}"
           f"{'S14':>7}{'S22':>7}{'cost%':>7}{'kap':>7}{'kapS14':>8}  flagy")
    print(hdr)
    print("-" * len(hdr))
    for ratio, r in sorted(rows, key=lambda x: -x[0])[:10]:
        print(f"{r[1]:<24}{r[4]:>8}{r[7]:>7}{ratio:>7.2f}{r[3]:>7}"
              f"{r[5]:>7}{r[6]:>7}{100 * r[12]:>6.1f}%{r[14]:>7}{r[15]:>8}  {r[24]}")
    print(f"\nCSV: {out_csv.relative_to(ROOT)} "
          f"({(_time.time() - t0) / 60:.1f} min)")
    return 0


CSV_COLS = [
    "scenario", "variant", "qty", "tp_pct", "thr", "sl_mult",
    "pnl_is", "pnl_oos", "pnl_s14", "pnl_s22", "dd_oos", "ratio_oos",
    "pnl_oos_slip0", "pnl_oos_slip05", "death_slip_pips",
    "cost_ratio_oos", "trades_oos", "win_oos_pct", "max_loss_streak_oos",
    "worst_day_oos", "worst_week_oos", "min_cap_oos", "underwater_days_oos",
    "pnl_oos_p08", "pnl_oos_p12", "flags",
]


def main() -> int:
    t0 = _time.time()
    print("OANDA LAB — načítavam dáta…", flush=True)
    ibkr = load_ibkr_csv(IBKR_CSV)
    duka = load_dukascopy_h1()
    dctx = sl.build_daily_ctx(duka, ibkr)
    ds_is = sl.prepare("IS", slice_years(ibkr, "IS", 2023, 2024), dctx, 3)
    ds_oos = sl.prepare("OOS", slice_years(ibkr, "OOS", 2025, 2026), dctx, 3)
    ds_s14 = sl.prepare("S14", slice_years(duka, "S14", 2014, 2015), dctx, 1)
    ds_s22 = sl.prepare("S22", slice_years(duka, "S22", 2021, 2022), dctx, 1)

    variants = build_variants()
    print(f"Variantov: {len(variants)} (×4 datasety ×3 slippage úrovne na OOS)",
          flush=True)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    f = open(OUT_CSV, "w", newline="")
    wr = csv.writer(f)
    wr.writerow(CSV_COLS)

    rows = []
    for k, v in enumerate(variants, 1):
        m_is = run_scalp_oanda(ds_is, dctx, v)
        m_oos = run_scalp_oanda(ds_oos, dctx, v)
        m_s14 = run_scalp_oanda(ds_s14, dctx, v)
        m_s22 = run_scalp_oanda(ds_s22, dctx, v)
        m_o0 = run_scalp_oanda(ds_oos, dctx, replace(v, slip=SLIP_LEVELS[0]))
        m_o5 = run_scalp_oanda(ds_oos, dctx, replace(v, slip=SLIP_LEVELS[2]))

        p08 = p12 = float("nan")
        if m_oos.pnl > 0:
            p08 = run_scalp_oanda(ds_oos, dctx,
                                  replace(v, sl_mult=v.sl_mult * 0.8)).pnl
            p12 = run_scalp_oanda(ds_oos, dctx,
                                  replace(v, sl_mult=v.sl_mult * 1.2)).pnl

        flags = []
        if m_oos.cost_ratio > 0.25:
            flags.append("COST_FAIL")
        if m_is.pnl > 0 and m_oos.pnl / ds_oos.years < 0.5 * m_is.pnl / ds_is.years:
            flags.append("OVERFIT")
        if m_is.pnl <= 0:
            flags.append("IS_NEGATIVE")
        if m_oos.pnl > 0 and not np.isnan(p08) and \
                min(p08, p12) < 0.4 * m_oos.pnl:
            flags.append("FRAGILE")
        if max(m_s14.min_cap, m_s22.min_cap) > STRESS_FATAL_CAP:
            flags.append("STRESS_FATAL")

        ratio = m_oos.pnl / m_oos.max_dd if m_oos.max_dd > 0 else 0.0
        ds = death_slip(m_o0.pnl, m_oos.pnl, m_o5.pnl)
        row = [v.mode, v.vid, int(v.qty), v.tp_pct, v.thr_mult, v.sl_mult,
               round(m_is.pnl), round(m_oos.pnl), round(m_s14.pnl),
               round(m_s22.pnl), round(m_oos.max_dd), round(ratio, 3),
               round(m_o0.pnl), round(m_o5.pnl), ds,
               round(m_oos.cost_ratio, 3), m_oos.trades,
               round(m_oos.win_rate, 1), m_oos.max_loss_streak,
               round(m_oos.worst_day), round(m_oos.worst_week),
               round(m_oos.min_cap), round(m_oos.underwater_days, 1),
               round(p08) if not np.isnan(p08) else "",
               round(p12) if not np.isnan(p12) else "",
               "|".join(flags)]
        wr.writerow(row)
        f.flush()
        rows.append((ratio, m_oos, row))
        print(f"[{k}/{len(variants)}] {v.vid:<24} IS {m_is.pnl:>8.0f}  "
              f"OOS {m_oos.pnl:>8.0f} (slip0 {m_o0.pnl:>8.0f} / slip5 "
              f"{m_o5.pnl:>8.0f})  death {ds:<8} "
              f"[{','.join(flags) or 'ok'}]  ({_time.time() - t0:.0f}s)",
              flush=True)
    f.close()

    print("\n=== TOP 5 podľa P/L / maxDD (OOS, slip 0.2) ===")
    hdr = (f"{'variant':<24}{'OOS':>8}{'DD':>7}{'ratio':>7}{'IS':>8}"
           f"{'death':>10}{'cost%':>7}{'streak':>7}{'w.deň':>8}{'kap':>7}  flagy")
    print(hdr)
    print("-" * len(hdr))
    for ratio, m, r in sorted(rows, key=lambda x: -x[0])[:5]:
        print(f"{r[1]:<24}{r[7]:>8}{r[10]:>7}{ratio:>7.2f}{r[6]:>8}"
              f"{r[14]:>10}{100 * r[15]:>6.1f}%{r[18]:>7}{r[19]:>8}{r[21]:>7}  {r[25]}")
    print(f"\nCSV: {OUT_CSV.relative_to(ROOT)} "
          f"({(_time.time() - t0) / 60:.1f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(grid_main() if "--grid" in sys.argv else main())
