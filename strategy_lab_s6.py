"""FÁZA 2 — S6 „BigMovePullback“: fade smerového pohybu 0.45–0.6 %.

Nadväzuje na pullback_study.py (Fáza 1): jediná stabilná podmnožina
s P(pullback ≥ 38 %) > 60 % sú pohyby veľkosti 0.45–0.6 % (62.5 %,
n=2 902, stabilné pre-2025 aj 2025+). S6 obchoduje PROTI takému pohybu
po stabilizácii.

Pravidlá (kauzálne, jedna pozícia naraz, max 1 vstup na nohu):
* noha = zigzag (reverz 0.15 %) ako vo Fáze 1; kvalifikácia pri vstupe:
  veľkosť ∈ [0.45 %, 0.60 %], trvanie ≤ 24 h
* vstupné varianty:
    cc  — prvá sviečka proti smeru (close proti smeru nohy), pokiaľ
          retrace z extrému < 38 % pohybu (nie sme neskoro)
    piv — potvrdenie pivotu: retrace z extrému > 0.15 % (bar zigzag flipu)
* SL za extrémom pohybu + rezerva max(0.05 %, 0.5× ATR14 daného TF)
* TP na frakcii hĺbky z Fázy 1: 38 % alebo 48 % (medián podmnožiny)
  pôvodného pohybu od extrému; vstup sa preskočí, ak by TP nebol
  ziskový voči vstupnej cene (stabilizácia už zjedla celý cieľ)
* time-stop 24 h (zatvorenie na close) — doplnok zadania, inak by
  pozície bez zásahu viseli neobmedzene
* TF varianty M5/H1/H4 (H1/H4 agregované z M5), pozície 25k/50k
* náklady IBKR (0.2 bps min $2 + 0.1 pipu spread), funding Fed−ECB

Stres 2014-15/2021-22 beží na Dukascopy H1 (M5 stres neexistuje —
M5 varianty používajú H1 stres ako proxy; H4 varianty H4 agregát).
Kill pravidlá ako vždy; fragilita = tp_frac ±20 % na OOS.

Beh: python3 strategy_lab_s6.py
Výstup: data/backtest_v2/results_lab_s6.csv + tabuľka.
"""

from __future__ import annotations

import csv
import sys
import time as _time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

import strategy_lab as sl
from backtest_v2 import IBKR_CSV, Bars, atr_wilder, load_dukascopy_h1, \
    load_ibkr_csv, slice_years
from pullback_study import aggregate
from trading.rates import daily_funding_usd
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
OUT_CSV = ROOT / "data" / "backtest_v2" / "results_lab_s6.csv"

REV_TH = 0.0015
SIZE_LO, SIZE_HI = 0.0045, 0.0060
MAX_LEG_H = 24.0
TIME_STOP_H = 24.0
COMM_BPS = 0.2e-4
COMM_MIN = 2.0
HALF_SPREAD = 0.000005
STRESS_FATAL_CAP = 100_000.0


@dataclass
class S6Cfg:
    tf: str = "M5"              # M5 | H1 | H4
    entry: str = "cc"           # cc | piv
    tp_frac: float = 0.48
    qty: float = 25_000

    @property
    def vid(self) -> str:
        return (f"S6_{self.tf}_{self.entry}_tp{self.tp_frac * 100:.0f}"
                f"_q{self.qty / 1000:.0f}k")


@dataclass
class S6Metrics:
    pnl: float = 0.0
    gross_win: float = 0.0
    costs: float = 0.0
    trades: int = 0
    wins: int = 0
    timeouts: int = 0
    max_dd: float = 0.0
    min_cap: float = 0.0
    underwater_days: float = 0.0
    avg_hold_h: float = 0.0

    @property
    def cost_ratio(self) -> float:
        return self.costs / self.gross_win if self.gross_win > 0 else 99.0

    @property
    def win_rate(self) -> float:
        return 100.0 * self.wins / self.trades if self.trades else 0.0


def run_s6(b: Bars, cfg: S6Cfg) -> S6Metrics:
    h, l, o, c, t = b.high, b.low, b.open, b.close, b.t
    atr = atr_wilder(b)
    n = len(t)
    m = S6Metrics()
    realized = funding = 0.0
    comm = spread = 0.0
    peak, peak_t = 0.0, None
    holds: list[float] = []
    pos = None                    # (side, entry, sl, tp, t0)
    prev_day = ""

    # kauzálny zigzag stav
    hi_p, hi_i = h[0], 0
    lo_p, lo_i = l[0], 0
    direction = 0
    start_p, start_i = c[0], 0
    ext_p, ext_i = c[0], 0
    consumed = False              # už sme vstúpili na aktuálnej nohe

    def fill_cost(q: float, price: float) -> float:
        nonlocal comm, spread
        cu = max(COMM_MIN, q * price * COMM_BPS)
        su = q * HALF_SPREAD
        comm += cu / price
        spread += su / price
        return (cu + su) / price

    def close_pos(price: float, tt: float, win: bool, timeout: bool = False):
        nonlocal realized, pos
        side, e, _sl, _tp, t0 = pos
        gross = (price - e) * cfg.qty / price if side == "long" \
            else (e - price) * cfg.qty / price
        m.gross_win += max(gross, 0.0)
        realized += gross - fill_cost(cfg.qty, price)
        m.trades += 1
        m.wins += int(win)
        m.timeouts += int(timeout)
        holds.append((tt - t0) / 3600)
        pos = None

    for i in range(n):
        day = datetime.fromtimestamp(int(t[i]), tz=timezone.utc).strftime("%Y-%m-%d")
        if pos is not None and day != prev_day and prev_day:
            funding += daily_funding_usd(day, pos[0], cfg.qty, c[i]) / c[i]
        prev_day = day

        # --- exit ------------------------------------------------------------
        if pos is not None:
            side, e, sl_p, tp_p, t0 = pos
            if side == "long":
                if l[i] <= sl_p:
                    close_pos(sl_p, t[i], False)
                elif h[i] >= tp_p:
                    close_pos(tp_p, t[i], True)
            else:
                if h[i] >= sl_p:
                    close_pos(sl_p, t[i], False)
                elif l[i] <= tp_p:
                    close_pos(tp_p, t[i], True)
            if pos is not None and (t[i] - t0) / 3600 > TIME_STOP_H:
                side, e, _s, _t, t0 = pos
                win = (c[i] > e) if side == "long" else (c[i] < e)
                close_pos(c[i], t[i], win, timeout=True)

        # --- zigzag update + vstup --------------------------------------------
        pivot = False
        if direction == 0:
            if h[i] > hi_p:
                hi_p, hi_i = h[i], i
            if l[i] < lo_p:
                lo_p, lo_i = l[i], i
            if hi_p - l[i] > hi_p * REV_TH:
                direction = -1
                start_p, start_i = hi_p, hi_i
                ext_p, ext_i = l[i], i
            elif h[i] - lo_p > lo_p * REV_TH:
                direction = 1
                start_p, start_i = lo_p, lo_i
                ext_p, ext_i = h[i], i
        elif direction == 1:
            if h[i] > ext_p:
                ext_p, ext_i = h[i], i
                consumed = consumed and False or consumed  # extrém rastie
            if ext_p - l[i] > ext_p * REV_TH:
                pivot = True
        else:
            if l[i] < ext_p:
                ext_p, ext_i = l[i], i
            if h[i] - ext_p > ext_p * REV_TH:
                pivot = True

        if direction != 0 and pos is None and not consumed and not np.isnan(atr[i]):
            move = abs(ext_p - start_p) / start_p
            dur_h = (t[ext_i] - t[start_i]) / 3600
            if SIZE_LO <= move <= SIZE_HI and dur_h <= MAX_LEG_H:
                move_abs = abs(ext_p - start_p)
                retr = (ext_p - c[i]) if direction == 1 else (c[i] - ext_p)
                trig = False
                if cfg.entry == "cc":
                    counter = (c[i] < o[i]) if direction == 1 else (c[i] > o[i])
                    trig = counter and 0 < retr < 0.38 * move_abs
                else:
                    trig = pivot
                if trig:
                    side = "short" if direction == 1 else "long"
                    E = ext_p
                    rez = max(0.0005 * E, 0.5 * atr[i])
                    if side == "short":
                        sl_p = E + rez
                        tp_p = E - cfg.tp_frac * move_abs
                        ok = tp_p < c[i]
                    else:
                        sl_p = E - rez
                        tp_p = E + cfg.tp_frac * move_abs
                        ok = tp_p > c[i]
                    if ok:
                        realized -= fill_cost(cfg.qty, c[i])
                        pos = (side, c[i], sl_p, tp_p, t[i])
                        consumed = True

        if pivot:                # dokonči flip nohy
            if direction == 1:
                start_p, start_i = ext_p, ext_i
                direction = -1
                ext_p, ext_i = l[i], i
            else:
                start_p, start_i = ext_p, ext_i
                direction = 1
                ext_p, ext_i = h[i], i
            consumed = False

        # --- equity ------------------------------------------------------------
        if pos is not None:
            side, e, *_ = pos
            fl = ((c[i] - e) if side == "long" else (e - c[i])) * cfg.qty / c[i]
            expo = cfg.qty
        else:
            fl = expo = 0.0
        eq = realized + funding + fl
        if peak_t is None or eq > peak:
            if peak_t is not None:
                m.underwater_days = max(m.underwater_days,
                                        (t[i] - peak_t) / 86400)
            peak, peak_t = eq, t[i]
        m.max_dd = max(m.max_dd, peak - eq)
        m.min_cap = max(m.min_cap, expo / 30.0 - eq)

    if peak_t is not None:
        m.underwater_days = max(m.underwater_days, (t[-1] - peak_t) / 86400)
    if pos is not None:
        side, e, *_ = pos
        realized += ((c[-1] - e) if side == "long" else (e - c[-1])) * cfg.qty / c[-1]
    m.pnl = realized + funding
    m.costs = comm + spread + max(-funding, 0.0)
    m.min_cap = max(m.min_cap, 0.0)
    m.avg_hold_h = float(np.mean(holds)) if holds else 0.0
    return m


CSV_COLS = ["variant", "tf", "entry", "tp_frac", "qty",
            "pnl_is", "pnl_oos", "pnl_s14", "pnl_s22",
            "dd_oos", "ratio_oos", "cost_ratio_oos", "trades_oos",
            "win_oos_pct", "timeouts_oos", "avg_hold_h_oos",
            "min_cap_oos", "underwater_days_oos",
            "pnl_oos_p08", "pnl_oos_p12", "flags"]


def main() -> int:
    t0 = _time.time()
    print("S6 BigMovePullback LAB — pripravujem dáta…", flush=True)
    ibkr = load_ibkr_csv(IBKR_CSV)
    duka = load_dukascopy_h1()

    def tf_bars(base: Bars, tf: str, name: str) -> Bars:
        if tf == "M5":
            return base
        return aggregate(base, 12 if tf == "H1" else 48, name)

    def tf_stress(tf: str, y0: int, y1: int) -> Bars:
        b = slice_years(duka, f"S{y0}", y0, y1)
        return b if tf in ("M5", "H1") else aggregate(b, 4, "H4")

    sets: dict[str, dict[str, Bars]] = {}
    for tf in ("M5", "H1", "H4"):
        sets[tf] = {
            "is": tf_bars(slice_years(ibkr, "IS", 2023, 2024), tf, "IS"),
            "oos": tf_bars(slice_years(ibkr, "OOS", 2025, 2026), tf, "OOS"),
            "s14": tf_stress(tf, 2014, 2015),
            "s22": tf_stress(tf, 2021, 2022),
        }
        print(f"  {tf}: IS {len(sets[tf]['is']):,} / OOS {len(sets[tf]['oos']):,} barov",
              flush=True)

    variants = [S6Cfg(tf, e, tp, q)
                for tf in ("M5", "H1", "H4")
                for e in ("cc", "piv")
                for tp in (0.38, 0.48)
                for q in (25_000, 50_000)]

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    f = open(OUT_CSV, "w", newline="")
    wr = csv.writer(f)
    wr.writerow(CSV_COLS)
    rows = []
    yrs_is, yrs_oos = 1.35, 1.55

    for k, cfg in enumerate(variants, 1):
        d = sets[cfg.tf]
        m_is = run_s6(d["is"], cfg)
        m_oos = run_s6(d["oos"], cfg)
        m_s14 = run_s6(d["s14"], cfg)
        m_s22 = run_s6(d["s22"], cfg)
        p08 = p12 = float("nan")
        if m_oos.pnl > 0:
            p08 = run_s6(d["oos"], replace(cfg, tp_frac=cfg.tp_frac * 0.8)).pnl
            p12 = run_s6(d["oos"], replace(cfg, tp_frac=cfg.tp_frac * 1.2)).pnl
        flags = []
        if m_oos.cost_ratio > 0.25:
            flags.append("COST_FAIL")
        if m_is.pnl > 0 and m_oos.pnl / yrs_oos < 0.5 * m_is.pnl / yrs_is:
            flags.append("OVERFIT")
        if m_is.pnl <= 0:
            flags.append("IS_NEGATIVE")
        if m_oos.pnl > 0 and not np.isnan(p08) and \
                min(p08, p12) < 0.4 * m_oos.pnl:
            flags.append("FRAGILE")
        if max(m_s14.min_cap, m_s22.min_cap) > STRESS_FATAL_CAP:
            flags.append("STRESS_FATAL")
        ratio = m_oos.pnl / m_oos.max_dd if m_oos.max_dd > 0 else 0.0
        rows.append((ratio, [cfg.vid, cfg.tf, cfg.entry, cfg.tp_frac,
                             int(cfg.qty), round(m_is.pnl), round(m_oos.pnl),
                             round(m_s14.pnl), round(m_s22.pnl),
                             round(m_oos.max_dd), round(ratio, 3),
                             round(m_oos.cost_ratio, 3), m_oos.trades,
                             round(m_oos.win_rate, 1), m_oos.timeouts,
                             round(m_oos.avg_hold_h, 1),
                             round(m_oos.min_cap),
                             round(m_oos.underwater_days, 1),
                             round(p08) if not np.isnan(p08) else "",
                             round(p12) if not np.isnan(p12) else "",
                             "|".join(flags)]))
        wr.writerow(rows[-1][1])
        f.flush()
        print(f"[{k}/{len(variants)}] {cfg.vid:<24} IS {m_is.pnl:>7.0f}  "
              f"OOS {m_oos.pnl:>7.0f}  S14 {m_s14.pnl:>6.0f}  "
              f"S22 {m_s22.pnl:>6.0f}  n={m_oos.trades:<4} "
              f"win {m_oos.win_rate:>4.1f}%  "
              f"[{','.join(flags) or 'ok'}]  ({_time.time() - t0:.0f}s)",
              flush=True)
    f.close()

    print("\n=== TOP 10 podľa P/L / maxDD (OOS) ===")
    hdr = (f"{'variant':<24}{'OOS':>8}{'DD':>7}{'ratio':>7}{'IS':>8}"
           f"{'S14':>7}{'S22':>7}{'n':>6}{'win%':>6}{'cost%':>7}  flagy")
    print(hdr)
    print("-" * len(hdr))
    for ratio, r in sorted(rows, key=lambda x: -x[0])[:10]:
        print(f"{r[0]:<24}{r[6]:>8}{r[9]:>7}{ratio:>7.2f}{r[5]:>8}"
              f"{r[7]:>7}{r[8]:>7}{r[12]:>6}{r[13]:>6}{100 * r[11]:>6.1f}%  {r[20]}")
    print(f"\nCSV: {OUT_CSV.relative_to(ROOT)} ({(_time.time() - t0) / 60:.1f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
