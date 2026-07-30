"""STRATEGY LAB — vetva G2B-5k-fine: hustý grid na malých pozíciách.

Otázka: kde pri zmenšovaní kroku gridu (0.05–0.15 %) a pozícii 5k/10k
prestáva stratégia G2B pokrývať náklady a koľko kapitálu reálne žiada.

Matica
------
* pozícia: 5 000 (+ 10 000 referenčne)
* krok short: 0.05 / 0.075 / 0.10 / 0.15 %  (long = 1.5× krok, ako G2B)
* TP: viazané na krok (TP = krok) aj fixné 0.10 % — obe verzie
* ostatné z G2B bez zmeny: pásma 1.12/1.16, ATR filter longov (2×ATR),
  kapacita 20+10, gap → TP na preskočenú úroveň

Nákladové modely
----------------
* razor  — cTrader/Pepperstone Razor: $3/strana/100k proporcionálne,
           min $0.04/príkaz; spread 0.15 pipu (pol na každý fill);
           swap z trading/rates.py. Bez slippage (TP = limitka na serveri;
           citlivosť na vstupný slip viď poznámka vo výstupe).
* ibkr   — referencia: 0.2 bps min $2/príkaz, spread 0.1 pipu ako
           v pôvodnom labe. Očakávanie: COST_FAIL pri malých pozíciách.

Kapitál (páka 1:30)
-------------------
kap_formula = 1.5 × (margin plnej kapacity + najhorší pozorovaný floating)
  * margin plnej kapacity = cap × qty / 30 (EUR, pre EURUSD nezávislé od ceny)
  * „s pravidlom" = beh s G8 poistkou (kurz > 2 % za 3-ročným extrémom →
    kapacita ×0.5, bez percentilového navyšovania); margin sa berie
    z pozorovanej max expozície toho behu
Popri formule sa reportuje aj empirické min_cap (max(margin − equity)).

Okná ako v hlavnom labe: IS 2023-24, OOS 2025-26 (M5), stres 2014-15,
2021-22 (H1). Kill: COST_FAIL (>25 % OOS), OVERFIT, IS_NEGATIVE,
STRESS_FATAL (>100k). FRAGILE sa nevyhodnocuje — celá matica JE sweep
kľúčového parametra (citlivosť čítaj z rozdielov susedných krokov).

Beh: python3 strategy_lab_g2b5k.py
Výstup: data/backtest_v2/results_g2b5k_fine.csv + tabuľky na stdout.
"""

from __future__ import annotations

import csv
import sys
import time as _time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from backtest_v2 import load_dukascopy_h1, load_ibkr_csv, slice_years, IBKR_CSV
from strategy_lab import (DailyCtx, Prepared, build_daily_ctx, prepare,
                          STRESS_FATAL_CAP)
from trading.rates import daily_funding_usd

ROOT = Path(__file__).resolve().parent
OUT_CSV = ROOT / "data" / "backtest_v2" / "results_g2b5k_fine.csv"

LEVERAGE = 30.0


# ===========================================================================
# Nákladové modely
# ===========================================================================

@dataclass(frozen=True)
class CostModel:
    name: str
    comm_per_100k: float        # USD/strana proporcionálne
    comm_min: float             # USD/príkaz
    half_spread: float          # cena (EURUSD) na fill

    def fill_usd(self, qty: float, price: float) -> tuple[float, float]:
        """(provízia, spread) v USD za jeden fill."""
        return (max(self.comm_min, qty / 100_000 * self.comm_per_100k),
                qty * self.half_spread)


RAZOR = CostModel("razor", 3.0, 0.04, 0.15e-4 / 2)
IBKR_REF = CostModel("ibkr", 0.0, 0.0, 0.1e-4 / 2)   # provízia cez bps nižšie
IBKR_BPS = 0.2e-4
IBKR_MIN = 2.0


def fill_cost_usd(cm: CostModel, qty: float, price: float) -> tuple[float, float]:
    if cm.name == "ibkr":
        return (max(IBKR_MIN, qty * price * IBKR_BPS), qty * cm.half_spread)
    return cm.fill_usd(qty, price)


# ===========================================================================
# Konfigurácia a metriky
# ===========================================================================

@dataclass
class FineCfg:
    qty: float
    step_s: float
    tp: float                    # short TP %; pri tp_mode="step" == step_s
    tp_mode: str                 # "step" | "fix10"
    tp_long: float | None = None  # long TP %; None = rovnaké ako tp
    step_l_ratio: float = 1.5
    cap_base: int = 20
    cap_reserve: int = 10
    atr_mult_long: float = 2.0
    band_lo: float = 1.1200
    band_hi: float = 1.1600
    news_pause_s: float = 0.0    # >0: žiadne nové vstupy X s od baru
    news_spike_mult: float = 4.0  # s range ≥ spike_mult × ATR (proxy news)

    @property
    def cap(self) -> int:
        return self.cap_base + self.cap_reserve


@dataclass
class FMetrics:
    pnl: float = 0.0
    gross_win: float = 0.0
    costs: float = 0.0
    commissions: float = 0.0
    spread: float = 0.0
    funding: float = 0.0
    trades: int = 0
    wins: int = 0
    max_dd: float = 0.0
    min_cap: float = 0.0
    max_expo: float = 0.0
    worst_float: float = 0.0     # najhorší floating (EUR, kladné číslo)
    underwater_days: float = 0.0
    open_end: int = 0
    failsafe_days: int = 0
    funding_long: float = 0.0    # EUR per smer (záporné = platí)
    funding_short: float = 0.0
    closes_long: int = 0
    closes_short: int = 0
    hold_sum_long: float = 0.0   # sekundy visenia zavretých pozícií
    hold_sum_short: float = 0.0

    @property
    def cost_ratio(self) -> float:
        return self.costs / self.gross_win if self.gross_win > 0 else 99.0


# ===========================================================================
# Engine — run_grid zo strategy_lab, zúžený na G2B (gap tp_skipped, fixné
# pásma) + injektovaný nákladový model, voliteľná G8 poistka (len ×0.5,
# bez navyšovania) a tracking najhoršieho floatingu.
# ===========================================================================

def run_fine(p: Prepared, dctx: DailyCtx, cfg: FineCfg, cm: CostModel,
             failsafe: bool, trace: list | None = None) -> FMetrics:
    b, atr = p.bars, p.atr
    m = FMetrics()
    realized = 0.0
    peak = 0.0
    peak_t: float | None = None
    step_l = cfg.step_s * cfg.step_l_ratio
    longs: list[tuple] = []
    shorts: list[tuple] = []
    ref_l = ref_s = b.close[0]
    last_l = last_s = 0.0
    prev_day = -1
    failsafe_prev = False
    spike_until = -np.inf        # news proxy: koniec pauzy po spike bare

    def fill_cost(qty: float, price: float) -> float:
        c_usd, s_usd = fill_cost_usd(cm, qty, price)
        m.commissions += c_usd / price
        m.spread += s_usd / price
        return (c_usd + s_usd) / price

    for i in range(len(b.t)):
        t, h, l, c = b.t[i], b.high[i], b.low[i], b.close[i]
        di = p.day_i[i]

        if di != prev_day and prev_day >= 0 and di >= 0:
            day = dctx.days[di]
            nd = max(di - prev_day, 1) if p.name.startswith("IBKR") else 1
            for e, q, *_ in longs:
                f = daily_funding_usd(day, "long", q, c) * nd / c
                m.funding += f
                m.funding_long += f
            for e, q, *_ in shorts:
                f = daily_funding_usd(day, "short", q, c) * nd / c
                m.funding += f
                m.funding_short += f
        if di >= 0:
            prev_day = di

        if longs:
            keep = []
            for e, q, tp, t0 in longs:
                if h >= tp:
                    gross = (tp - e) * q / tp
                    m.gross_win += max(gross, 0.0)
                    realized += gross - fill_cost(q, tp)
                    m.trades += 1
                    m.wins += 1
                    m.closes_long += 1
                    m.hold_sum_long += t - t0
                else:
                    keep.append((e, q, tp, t0))
            longs = keep
            if not longs:
                ref_l = c
        if shorts:
            keep = []
            for e, q, tp, t0 in shorts:
                if l <= tp:
                    gross = (e - tp) * q / tp
                    m.gross_win += max(gross, 0.0)
                    realized += gross - fill_cost(q, tp)
                    m.trades += 1
                    m.wins += 1
                    m.closes_short += 1
                    m.hold_sum_short += t - t0
                else:
                    keep.append((e, q, tp, t0))
            shorts = keep
            if not shorts:
                ref_s = c

        ref_l = max(ref_l, h)
        ref_s = min(ref_s, l)

        a = atr[i]
        can_enter = not np.isnan(a)
        if cfg.news_pause_s > 0 and not np.isnan(a):
            if h - l >= cfg.news_spike_mult * a:
                spike_until = t + cfg.news_pause_s
            can_enter = can_enter and t >= spike_until
        allow_long = c < cfg.band_hi
        allow_short = c > cfg.band_lo

        cap_l = cap_s = cfg.cap
        if failsafe and di >= 0 and not np.isnan(dctx.hi3y[di]):
            fs = (c > dctx.hi3y[di] * 1.02) or (c < dctx.lo3y[di] * 0.98)
            if fs:
                cap_l = cap_s = int(cfg.cap * 0.5)
                if not failsafe_prev:
                    m.failsafe_days += 1
            failsafe_prev = fs

        if can_enter:
            if allow_long and len(longs) < cap_l:
                drop = ref_l - c
                trigger = max(ref_l * step_l, cfg.atr_mult_long * a)
                unlock = (len(longs) < cfg.cap_base
                          or abs(c - last_l) > 2.0 * a)
                if drop >= trigger and unlock:
                    k = int(drop / (ref_l * step_l)) if step_l > 0 else 1
                    tp_l = cfg.tp_long if cfg.tp_long is not None else cfg.tp
                    tp = c * (1 + step_l) if k >= 2 else c * (1 + tp_l)
                    realized -= fill_cost(cfg.qty, c)
                    longs.append((c, cfg.qty, tp, t))
                    if trace is not None:
                        trace.append((t, "L", round(c, 5), round(tp, 5)))
                    last_l = c
                    ref_l = c
            if allow_short and len(shorts) < cap_s:
                rise = c - ref_s
                unlock = (len(shorts) < cfg.cap_base
                          or abs(c - last_s) > 2.0 * a)
                if rise >= ref_s * cfg.step_s and unlock:
                    k = int(rise / (ref_s * cfg.step_s))
                    tp = c * (1 - cfg.step_s) if k >= 2 else c * (1 - cfg.tp)
                    realized -= fill_cost(cfg.qty, c)
                    shorts.append((c, cfg.qty, tp, t))
                    if trace is not None:
                        trace.append((t, "S", round(c, 5), round(tp, 5)))
                    last_s = c
                    ref_s = c

        float_eur = (sum((c - e) * q for e, q, *_ in longs)
                     + sum((e - c) * q for e, q, *_ in shorts)) / c
        expo = sum(q for _, q, *_ in longs) + sum(q for _, q, *_ in shorts)
        m.worst_float = max(m.worst_float, -float_eur)
        m.max_expo = max(m.max_expo, expo)
        equity = realized + float_eur
        if peak_t is None or equity > peak:
            if peak_t is not None:
                m.underwater_days = max(m.underwater_days, (t - peak_t) / 86400)
            peak = equity
            peak_t = t
        m.max_dd = max(m.max_dd, peak - equity)
        m.min_cap = max(m.min_cap, expo / LEVERAGE - equity)

    c_end = b.close[-1]
    float_eur = (sum((c_end - e) * q for e, q, *_ in longs)
                 + sum((e - c_end) * q for e, q, *_ in shorts)) / c_end
    if peak_t is not None:
        m.underwater_days = max(m.underwater_days, (b.t[-1] - peak_t) / 86400)
    m.pnl = realized + float_eur
    m.open_end = len(longs) + len(shorts)
    m.costs = m.commissions + m.spread + max(-m.funding, 0.0)
    m.min_cap = max(m.min_cap, 0.0)
    return m


# ===========================================================================
# Kapitál pri 1:30
# ===========================================================================

def cap_need(cfg: FineCfg, m: FMetrics, *, use_observed_expo: bool) -> float:
    """1.5 × (margin plnej kapacity + najhorší floating).

    Pri behu s poistkou je „plná kapacita" režimovo závislá → margin sa
    berie z pozorovanej max expozície; bez poistky teoretická (cap × qty).
    """
    margin = (m.max_expo if use_observed_expo else cfg.cap * cfg.qty) / LEVERAGE
    return 1.5 * (margin + m.worst_float)


# ===========================================================================
# Main
# ===========================================================================

CSV_COLS = [
    "variant", "qty", "step_bp", "tp_mode", "tp_bp", "cost_model",
    "pnl_is", "pnl_oos", "pnl_s14", "pnl_s22",
    "dd_is", "dd_oos", "ratio_oos",
    "cost_ratio_is", "cost_ratio_oos",
    "trades_is", "trades_oos", "cyc_day_is", "cyc_day_oos",
    "gross_per_cyc_oos", "win_oos_pct",
    "uw_days_is", "uw_days_oos",
    "kap_norm", "kap_norm_rule",
    "kap_s14", "kap_s14_rule", "kap_s22", "kap_s22_rule",
    "mincap_oos", "mincap_s14", "mincap_s22",
    "max_expo_oos", "funding_oos", "open_end_oos",
    "flags",
]


def main() -> int:
    t0 = _time.time()
    print("G2B-5k-fine LAB — načítavam dáta…", flush=True)
    ibkr = load_ibkr_csv(IBKR_CSV)
    duka = load_dukascopy_h1()
    dctx = build_daily_ctx(duka, ibkr)

    ds_is = prepare("IBKR_IS_2023-24", slice_years(ibkr, "IS", 2023, 2024), dctx, 3)
    ds_oos = prepare("IBKR_OOS_2025-26", slice_years(ibkr, "OOS", 2025, 2026), dctx, 3)
    ds_s14 = prepare("STRES_2014-15", slice_years(duka, "S14", 2014, 2015), dctx, 1)
    ds_s22 = prepare("STRES_2021-22", slice_years(duka, "S22", 2021, 2022), dctx, 1)
    for d in (ds_is, ds_oos, ds_s14, ds_s22):
        print(f"  {d.name}: {len(d.bars):,} barov, {d.years:.2f} r.", flush=True)

    variants: list[FineCfg] = []
    for qty in (5_000, 10_000):
        for st in (0.0005, 0.00075, 0.0010, 0.0015):
            for tp_mode in ("step", "fix10"):
                tp = st if tp_mode == "step" else 0.0010
                variants.append(FineCfg(qty=qty, step_s=st, tp=tp,
                                        tp_mode=tp_mode))

    combos = [(cfg, cm) for cfg in variants for cm in (RAZOR, IBKR_REF)]
    print(f"Variantov: {len(variants)} × 2 nákladové modely = {len(combos)}; "
          f"behy: {len(combos)} × 8 okien.", flush=True)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fcsv = open(OUT_CSV, "w", newline="")
    writer = csv.writer(fcsv)
    writer.writerow(CSV_COLS)

    rows = []
    for k, (cfg, cm) in enumerate(combos, 1):
        vid = (f"F_q{cfg.qty // 1000}k_st{cfg.step_s * 1e4:g}bp_"
               f"tp{'step' if cfg.tp_mode == 'step' else '10bp'}_{cm.name}")

        m_is = run_fine(ds_is, dctx, cfg, cm, False)
        m_oos = run_fine(ds_oos, dctx, cfg, cm, False)
        m_s14 = run_fine(ds_s14, dctx, cfg, cm, False)
        m_s22 = run_fine(ds_s22, dctx, cfg, cm, False)
        r_is = run_fine(ds_is, dctx, cfg, cm, True)
        r_oos = run_fine(ds_oos, dctx, cfg, cm, True)
        r_s14 = run_fine(ds_s14, dctx, cfg, cm, True)
        r_s22 = run_fine(ds_s22, dctx, cfg, cm, True)

        flags = []
        if m_oos.cost_ratio > 0.25:
            flags.append("COST_FAIL")
        if m_is.pnl > 0 and m_oos.pnl / ds_oos.years < 0.5 * m_is.pnl / ds_is.years:
            flags.append("OVERFIT")
        if m_is.pnl <= 0:
            flags.append("IS_NEGATIVE")
        if max(m_s14.min_cap, m_s22.min_cap) > STRESS_FATAL_CAP:
            flags.append("STRESS_FATAL")

        kap_norm = max(cap_need(cfg, m_is, use_observed_expo=False),
                       cap_need(cfg, m_oos, use_observed_expo=False))
        kap_norm_rule = max(cap_need(cfg, r_is, use_observed_expo=True),
                            cap_need(cfg, r_oos, use_observed_expo=True))
        ratio = m_oos.pnl / m_oos.max_dd if m_oos.max_dd > 0 else 0.0
        days_is = ds_is.years * 365.25
        days_oos = ds_oos.years * 365.25

        row = [vid, cfg.qty, round(cfg.step_s * 1e4, 1), cfg.tp_mode,
               round(cfg.tp * 1e4, 1), cm.name,
               round(m_is.pnl), round(m_oos.pnl),
               round(m_s14.pnl), round(m_s22.pnl),
               round(m_is.max_dd), round(m_oos.max_dd), round(ratio, 3),
               round(m_is.cost_ratio, 3), round(m_oos.cost_ratio, 3),
               m_is.trades, m_oos.trades,
               round(m_is.trades / days_is, 2), round(m_oos.trades / days_oos, 2),
               round(m_oos.gross_win / m_oos.trades, 2) if m_oos.trades else 0,
               round(100 * m_oos.wins / m_oos.trades, 1) if m_oos.trades else 0,
               round(m_is.underwater_days, 1), round(m_oos.underwater_days, 1),
               round(kap_norm), round(kap_norm_rule),
               round(cap_need(cfg, m_s14, use_observed_expo=False)),
               round(cap_need(cfg, r_s14, use_observed_expo=True)),
               round(cap_need(cfg, m_s22, use_observed_expo=False)),
               round(cap_need(cfg, r_s22, use_observed_expo=True)),
               round(m_oos.min_cap), round(m_s14.min_cap), round(m_s22.min_cap),
               round(m_oos.max_expo), round(m_oos.funding), m_oos.open_end,
               "|".join(flags)]
        writer.writerow(row)
        fcsv.flush()
        rows.append(row)
        print(f"[{k}/{len(combos)}] {vid:<34} "
              f"IS {m_is.pnl:>8.0f}  OOS {m_oos.pnl:>8.0f}  "
              f"cyc/d {m_oos.trades / days_oos:>5.2f}  "
              f"cost {100 * m_oos.cost_ratio:>5.1f}%  "
              f"kap {kap_norm:>7.0f}  [{','.join(flags) or 'ok'}]  "
              f"({_time.time() - t0:.0f}s)", flush=True)

    fcsv.close()

    print("\n=== POROVNANIE (razor) — zoradené podľa ratio OOS ===", flush=True)
    hdr = (f"{'variant':<34}{'OOS P/L':>8}{'OOS DD':>7}{'ratio':>6}"
           f"{'cyc/d':>6}{'€/cyk':>6}{'cost%':>6}{'win%':>6}"
           f"{'uwOOS':>6}{'kapN':>7}{'kapS22':>7}{'kapS22r':>8}  flagy")
    print(hdr)
    print("-" * len(hdr))
    for r in sorted((r for r in rows if r[5] == "razor"),
                    key=lambda r: -r[12]):
        print(f"{r[0]:<34}{r[7]:>8}{r[11]:>7}{r[12]:>6.2f}"
              f"{r[18]:>6}{r[19]:>6}{100 * r[14]:>5.1f}%{r[20]:>6}"
              f"{r[22]:>6}{r[23]:>7}{r[27]:>7}{r[28]:>8}  {r[35]}")
    print(f"\nCSV: {OUT_CSV.relative_to(ROOT)}  "
          f"(celkový čas {(_time.time() - t0) / 60:.1f} min)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
