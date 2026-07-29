"""FÁZA 1 — tri deskriptívne štúdie (A–C) + priamy test D na EURUSD.

Dáta: Dukascopy H1 2013 → 2023-08 + IBKR M5 agregované na H1 → jedna
súvislá hodinová séria 2013–2026; denná séria sa z nej skladá po UTC dňoch.

A) VolCompression — po vstupe denného ATR(14) do najnižšieho decilu
   (kĺzavé 60-dňové okno, kauzálne) zmeria distribúciu |pohybu| a range
   nasledujúcich 1/2/5 dní vs zvyšok dát. Verdikt: expanzia áno/nie.

B) TurnOfMonth — drift a smerovosť v okne (posledné 2 + prvé 2 obchodné
   dni mesiaca) vs zvyšok; Welchov t-test (p približne cez normálnu
   aproximáciu, pri n v stovkách OK) a stabilita po rokoch.

C) FridayEffect — drift piatok po 14:00 UTC vs rovnaké okno po-št;
   osobitne posledná obchodná hodina piatku.

D) FollowTheBear — H1 short-only, 6–10 GMT, ut–št, pinbar/shooting star
   (open aj close v spodnej polovici sviečky), SL 2 pipy nad high,
   TP 1:1, max 1 pozícia, časová poistka 5 dní (v praxi takmer neviaže).
   Plná simulácia s nákladmi (ECN aj IBKR model), IS/OOS/stres, kill
   pravidlá ako v labe.

Beh: python3 study_phase1.py
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timezone

import numpy as np

from backtest_v2 import IBKR_CSV, Bars, load_dukascopy_h1, load_ibkr_csv
from pullback_study import aggregate

PIP = 0.0001


# ---------------------------------------------------------------- dáta
def merged_h1() -> Bars:
    ibkr = load_ibkr_csv(IBKR_CSV)
    duka = load_dukascopy_h1()
    cut = ibkr.t[0]
    m = duka.t < cut
    ih = aggregate(ibkr, 12, "H1")
    return Bars("H1_2013_2026",
                np.concatenate([duka.t[m], ih.t]),
                np.concatenate([duka.open[m], ih.open]),
                np.concatenate([duka.high[m], ih.high]),
                np.concatenate([duka.low[m], ih.low]),
                np.concatenate([duka.close[m], ih.close]))


def to_daily(h1: Bars):
    """UTC denné OHLC + metadáta (dátum, rok, mesiac)."""
    days, o, h, l, c = [], [], [], [], []
    cur = None
    for i in range(len(h1.t)):
        d = datetime.fromtimestamp(int(h1.t[i]), tz=timezone.utc).date()
        if d != cur:
            days.append(d)
            o.append(h1.open[i]); h.append(h1.high[i])
            l.append(h1.low[i]); c.append(h1.close[i])
            cur = d
        else:
            h[-1] = max(h[-1], h1.high[i])
            l[-1] = min(l[-1], h1.low[i])
            c[-1] = h1.close[i]
    return (days, np.array(o), np.array(h), np.array(l), np.array(c))


def welch(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """(t, p) — Welch; p dvojstranné cez normálnu aproximáciu."""
    nx, ny = len(x), len(y)
    if nx < 3 or ny < 3:
        return 0.0, 1.0
    vx, vy = x.var(ddof=1), y.var(ddof=1)
    t = (x.mean() - y.mean()) / math.sqrt(vx / nx + vy / ny)
    p = math.erfc(abs(t) / math.sqrt(2))
    return t, p


def atr_daily(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14):
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = np.full(len(c), np.nan)
    if len(c) <= n:
        return atr
    atr[n - 1] = tr[:n].mean()
    for i in range(n, len(c)):
        atr[i] = atr[i - 1] * (n - 1) / n + tr[i] / n
    return atr


# ---------------------------------------------------------------- A
def study_a(days, do, dh, dl, dc) -> None:
    print("=" * 74)
    print("A) VOLCOMPRESSION — expanzia po stlačení volatility?")
    atr = atr_daily(dh, dl, dc)
    n = len(dc)
    W = 60
    rank = np.full(n, np.nan)
    for i in range(W + 14, n):
        win = atr[i - W + 1:i + 1]
        rank[i] = 100.0 * (win < atr[i]).mean()

    low = rank <= 10.0                      # najnižší decil
    prev = np.concatenate(([False], low[:-1]))
    enter = low & ~prev                     # deň VSTUPU do decilu

    for k, lbl in ((1, "24 h"), (2, "48 h"), (5, "5 dní")):
        fmove = np.full(n, np.nan)
        frange = np.full(n, np.nan)
        for i in range(n - k):
            fmove[i] = abs(dc[i + k] / dc[i] - 1) * 100
            frange[i] = (dh[i + 1:i + k + 1].max() - dl[i + 1:i + k + 1].min()) / dc[i] * 100
        valid = ~np.isnan(fmove) & ~np.isnan(rank)
        a = fmove[valid & enter]
        b = fmove[valid & ~low]
        ra = frange[valid & enter]
        rb = frange[valid & ~low]
        t, p = welch(a, b)
        print(f"  {lbl:>5}: |pohyb| po vstupe do decilu: medián {np.median(a):.3f} % "
              f"(σ {a.std():.3f}, n={len(a)})  vs zvyšok {np.median(b):.3f} % "
              f"(σ {b.std():.3f})  → t={t:+.2f}, p≈{p:.4f}")
        print(f"         range: {np.median(ra):.3f} % vs {np.median(rb):.3f} %")
    a5 = None
    print("  → verdikt v závere skriptu")


# ---------------------------------------------------------------- B
def study_b(days, dc) -> None:
    print("=" * 74)
    print("B) TURNOFMONTH — drift na prelome mesiaca?")
    n = len(dc)
    ret = np.full(n, np.nan)
    ret[1:] = (dc[1:] / dc[:-1] - 1) * 1e4          # bps

    # indexy dní v mesiaci
    tom = np.zeros(n, dtype=bool)
    by_month: dict[tuple, list[int]] = {}
    for i, d in enumerate(days):
        by_month.setdefault((d.year, d.month), []).append(i)
    for key, idxs in by_month.items():
        for i in idxs[-2:]:
            tom[i] = True                            # posledné 2 dni mesiaca
        for i in idxs[:2]:
            tom[i] = True                            # prvé 2 dni mesiaca

    valid = ~np.isnan(ret)
    a, b = ret[valid & tom], ret[valid & ~tom]
    t, p = welch(a, b)
    print(f"  ToM dni: n={len(a)}, priemer {a.mean():+.2f} bps/deň, "
          f"medián {np.median(a):+.2f}, % kladných {100 * (a > 0).mean():.1f} %")
    print(f"  zvyšok : n={len(b)}, priemer {b.mean():+.2f} bps/deň, "
          f"medián {np.median(b):+.2f}, % kladných {100 * (b > 0).mean():.1f} %")
    print(f"  Welch t={t:+.2f}, p≈{p:.4f}")
    print("  stabilita po rokoch (priemer ToM bps/deň):")
    line = []
    pos_years = 0
    years = sorted({d.year for d in days})
    for y in years:
        sel = np.array([d.year == y for d in days]) & tom & valid
        if sel.sum() < 8:
            continue
        m = ret[sel].mean()
        pos_years += m > 0
        line.append(f"{y}:{m:+.1f}")
    print("   " + "  ".join(line))
    print(f"   kladných rokov: {pos_years}/{len(line)}")


# ---------------------------------------------------------------- C
def study_c(h1: Bars) -> None:
    print("=" * 74)
    print("C) FRIDAYEFFECT — drift piatok popoludní?")
    n = len(h1.t)
    wd = np.empty(n, dtype=int)
    hr = np.empty(n, dtype=int)
    dt_date = []
    for i in range(n):
        dt = datetime.fromtimestamp(int(h1.t[i]), tz=timezone.utc)
        wd[i] = dt.weekday()
        hr[i] = dt.hour
        dt_date.append(dt.date())
    ret = np.full(n, np.nan)
    ret[1:] = (h1.close[1:] / h1.close[:-1] - 1) * 1e4

    # popoludňajšie okno ≥14:00 UTC, agregované per deň
    day_sum: dict[tuple, float] = {}
    for i in range(1, n):
        if hr[i] >= 14 and not np.isnan(ret[i]):
            key = (dt_date[i], wd[i])
            day_sum[key] = day_sum.get(key, 0.0) + ret[i]
    fri = np.array([v for (d, w), v in day_sum.items() if w == 4])
    oth = np.array([v for (d, w), v in day_sum.items() if w < 4])
    t, p = welch(fri, oth)
    print(f"  piatok ≥14:00: n={len(fri)}, priemer {fri.mean():+.2f} bps, "
          f"medián {np.median(fri):+.2f}, % kladných {100 * (fri > 0).mean():.1f} %")
    print(f"  po–št ≥14:00 : n={len(oth)}, priemer {oth.mean():+.2f} bps, "
          f"medián {np.median(oth):+.2f}, % kladných {100 * (oth > 0).mean():.1f} %")
    print(f"  Welch t={t:+.2f}, p≈{p:.4f}")

    # posledná hodina pred víkendom (posledný bar piatku)
    last_by_day: dict = {}
    for i in range(n):
        if wd[i] == 4:
            last_by_day[dt_date[i]] = i
    lh = []
    for i in last_by_day.values():
        lh.append((h1.close[i] / h1.open[i] - 1) * 1e4)
    lh = np.array(lh)
    print(f"  posledná hodina piatku: n={len(lh)}, priemer {lh.mean():+.2f} bps, "
          f"% kladných {100 * (lh > 0).mean():.1f} %, σ {lh.std():.1f}")


# ---------------------------------------------------------------- D
def study_d(h1: Bars) -> None:
    print("=" * 74)
    print("D) FOLLOWTHEBEAR — priamy test (short-only pinbar, 6–10 GMT, ut–št)")
    n = len(h1.t)
    wd = np.empty(n, dtype=int)
    hr = np.empty(n, dtype=int)
    yr = np.empty(n, dtype=int)
    for i in range(n):
        dt = datetime.fromtimestamp(int(h1.t[i]), tz=timezone.utc)
        wd[i], hr[i], yr[i] = dt.weekday(), dt.hour, dt.year

    def run(mask, qty, model, sl_buffer=2 * PIP):
        pos = None
        pnl = comm = spread = slip = 0.0
        ntr = wins = 0
        gross_win = 0.0
        equity_min, eq = 0.0, 0.0
        for i in range(n):
            h, l, c, o = h1.high[i], h1.low[i], h1.close[i], h1.open[i]
            if pos is not None:
                e, slp, tp, t0 = pos
                ex = None
                win = False
                market_exit = False
                if h >= slp:
                    ex, market_exit = slp, True
                elif l <= tp:
                    ex, win = tp, True
                elif h1.t[i] - t0 > 5 * 86400:
                    ex, market_exit = c, True
                    win = c < e
                if ex is not None:
                    gross = (e - ex) * qty / ex
                    gross_win += max(gross, 0.0)
                    cst = cost(qty, ex, model, market_exit)
                    pnl += gross - cst
                    eq = pnl
                    equity_min = min(equity_min, eq)
                    ntr += 1
                    wins += win
                    pos = None
            if pos is None and mask[i]:
                rng = h - l
                if rng <= 0:
                    continue
                half = l + 0.5 * rng
                if o <= half and c <= half:
                    e = c
                    slp = h + sl_buffer
                    tp = e - (slp - e)
                    if tp <= 0:
                        continue
                    pnl -= cost(qty, e, model, True)
                    pos = (e, slp, tp, h1.t[i])
        return {"pnl": pnl, "n": ntr,
                "win": 100 * wins / ntr if ntr else 0.0,
                "gross_win": gross_win,
                "costs": comm_total[0]}

    comm_total = [0.0]

    def cost(qty, price, model, market):
        if model == "ecn":
            c = qty / 100_000 * 3.5 + qty * 0.15e-4 / 2 + (qty * 0.2e-4 if market else 0.0)
        else:
            c = max(2.0, qty * price * 0.2e-4) + qty * 0.05e-4
        comm_total[0] += c / price
        return c / price

    entry_mask = np.isin(wd, (1, 2, 3)) & np.isin(hr, (6, 7, 8, 9))

    windows = [("IS 2023-24", (yr >= 2023) & (yr <= 2024)),
               ("OOS 2025-26", yr >= 2025),
               ("STRES 2014-15", (yr >= 2014) & (yr <= 2015)),
               ("STRES 2021-22", (yr >= 2021) & (yr <= 2022))]

    print(f"  {'okno':<14}{'model':<7}{'qty':>7}{'obch.':>7}{'win%':>7}"
          f"{'P/L €':>10}{'náklady €':>11}{'cost%':>7}")
    results = {}
    for wlbl, wmask in windows:
        for model, qty in (("ecn", 2_000), ("ecn", 25_000), ("ibkr", 25_000)):
            comm_total[0] = 0.0
            r = run(entry_mask & wmask, qty, model)
            cr = r["costs"] / r["gross_win"] if r["gross_win"] > 0 else 99
            results[(wlbl, model, qty)] = r
            print(f"  {wlbl:<14}{model:<7}{qty:>7,}{r['n']:>7}{r['win']:>7.1f}"
                  f"{r['pnl']:>10.2f}{r['costs']:>11.2f}{100 * cr:>6.1f}%")

    # kill pravidlá pre hlavný variant (ecn 2k)
    is_p = results[("IS 2023-24", "ecn", 2000)]["pnl"]
    oos_p = results[("OOS 2025-26", "ecn", 2000)]["pnl"]
    flags = []
    if is_p <= 0:
        flags.append("IS_NEGATIVE")
    if oos_p <= 0:
        flags.append("OOS_NEGATIVE")
    r = results[("OOS 2025-26", "ecn", 2000)]
    cr = r["costs"] / r["gross_win"] if r["gross_win"] > 0 else 99
    if cr > 0.25:
        flags.append("COST_FAIL")
    if oos_p > 0:
        for mlt, tag in ((0.8, "p08"), (1.2, "p12")):
            comm_total[0] = 0.0
            rr = run(entry_mask & (yr >= 2025), 2000, "ecn",
                     sl_buffer=2 * PIP * mlt)
            print(f"  fragilita SL×{mlt}: P/L {rr['pnl']:.2f}")
    print(f"  KILL FLAGY (ecn 2k): {', '.join(flags) or 'žiadne'}")


def main() -> int:
    print("FÁZA 1 — načítavam dáta…")
    h1 = merged_h1()
    print(f"  H1 séria: {len(h1.t):,} barov "
          f"({datetime.fromtimestamp(int(h1.t[0]), tz=timezone.utc):%Y-%m} → "
          f"{datetime.fromtimestamp(int(h1.t[-1]), tz=timezone.utc):%Y-%m})")
    days, do, dh, dl, dc = to_daily(h1)
    print(f"  denná séria: {len(days):,} dní")
    study_a(days, do, dh, dl, dc)
    study_b(days, dc)
    study_c(h1)
    study_d(h1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
