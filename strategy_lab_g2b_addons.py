"""G2B doplnky: (a) symetrický long krok, (b) citlivosť na news pauzu.

(a) G2B-sym  — long krok −0.15 % pri short +0.15 % (step_l_ratio 1.0
    namiesto 1.5), TP 0.10 %, ostatné G2B bez zmeny. POZOR: ATR filter
    longov ostáva (trigger = max(krok_long, 2×ATR)) — na M5 je 2×ATR
    typicky ~0.16–0.18 %, takže symetria sa prejaví hlavne cez gap
    k-počítanie a bližšie gap-TP, nie cez hustejšie long vstupy.
(b) G2B-news — proxy news pauza: bar s range ≥ 4×ATR(14) → žiadne nové
    vstupy od tohto baru do 1 h po ňom (kauzálna aproximácia „±30 min
    okolo správy"; dopredu sa pauzovať bez kalendára nedá). Konvencia
    spike baru zhodná so scalpom S4 v hlavnom labe.

Setupy: 25k/IBKR (kotva na G2B_gap_tp_skip v results_lab.csv)
        a 5k/Razor (aktuálna cTrader realita).
Okná: IS 2023-24, OOS 2025-26, stres 2014-15, 2021-22. Bez G8 poistky
(zhoda s G2B baseline hlavného labu).

Beh: python3 strategy_lab_g2b_addons.py
Výstup: data/backtest_v2/results_g2b_addons.csv + tabuľka na stdout.
"""

from __future__ import annotations

import csv
import sys
import time as _time
from dataclasses import replace
from pathlib import Path

from backtest_v2 import load_dukascopy_h1, load_ibkr_csv, slice_years, IBKR_CSV
from strategy_lab import build_daily_ctx, prepare
from strategy_lab_g2b5k import (CostModel, FineCfg, IBKR_REF, RAZOR, run_fine)

ROOT = Path(__file__).resolve().parent
OUT_CSV = ROOT / "data" / "backtest_v2" / "results_g2b_addons.csv"

CSV_COLS = [
    "variant", "qty", "cost_model",
    "pnl_is", "pnl_oos", "pnl_s14", "pnl_s22",
    "dd_is", "dd_oos", "dd_s14", "dd_s22", "ratio_oos",
    "cost_ratio_oos", "trades_is", "trades_oos",
    "cyc_day_oos", "win_oos_pct", "uw_days_is", "uw_days_oos",
    "worst_float_oos", "worst_float_s22", "max_expo_oos", "open_end_oos",
]


def main() -> int:
    t0 = _time.time()
    print("G2B ADDONS — načítavam dáta…", flush=True)
    ibkr = load_ibkr_csv(IBKR_CSV)
    duka = load_dukascopy_h1()
    dctx = build_daily_ctx(duka, ibkr)

    ds_is = prepare("IBKR_IS_2023-24", slice_years(ibkr, "IS", 2023, 2024), dctx, 3)
    ds_oos = prepare("IBKR_OOS_2025-26", slice_years(ibkr, "OOS", 2025, 2026), dctx, 3)
    ds_s14 = prepare("STRES_2014-15", slice_years(duka, "S14", 2014, 2015), dctx, 1)
    ds_s22 = prepare("STRES_2021-22", slice_years(duka, "S22", 2021, 2022), dctx, 1)

    def base(qty: float) -> FineCfg:
        return FineCfg(qty=qty, step_s=0.0015, tp=0.0010, tp_mode="fix10")

    variants = [
        ("G2B_baseline", lambda q: base(q)),
        ("G2B_sym_long15", lambda q: replace(base(q), step_l_ratio=1.0)),
        ("G2B_news_pause1h", lambda q: replace(base(q), news_pause_s=3600.0)),
    ]
    setups: list[tuple[float, CostModel]] = [(25_000, IBKR_REF), (5_000, RAZOR)]

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fcsv = open(OUT_CSV, "w", newline="")
    writer = csv.writer(fcsv)
    writer.writerow(CSV_COLS)

    rows = []
    for qty, cm in setups:
        for name, make in variants:
            cfg = make(qty)
            m_is = run_fine(ds_is, dctx, cfg, cm, False)
            m_oos = run_fine(ds_oos, dctx, cfg, cm, False)
            m_s14 = run_fine(ds_s14, dctx, cfg, cm, False)
            m_s22 = run_fine(ds_s22, dctx, cfg, cm, False)
            ratio = m_oos.pnl / m_oos.max_dd if m_oos.max_dd > 0 else 0.0
            days_oos = ds_oos.years * 365.25
            row = [name, qty, cm.name,
                   round(m_is.pnl), round(m_oos.pnl),
                   round(m_s14.pnl), round(m_s22.pnl),
                   round(m_is.max_dd), round(m_oos.max_dd),
                   round(m_s14.max_dd), round(m_s22.max_dd), round(ratio, 3),
                   round(m_oos.cost_ratio, 3), m_is.trades, m_oos.trades,
                   round(m_oos.trades / days_oos, 2),
                   round(100 * m_oos.wins / m_oos.trades, 1) if m_oos.trades else 0,
                   round(m_is.underwater_days, 1), round(m_oos.underwater_days, 1),
                   round(m_oos.worst_float), round(m_s22.worst_float),
                   round(m_oos.max_expo), m_oos.open_end]
            writer.writerow(row)
            fcsv.flush()
            rows.append(row)
            print(f"{name:<18} q{qty // 1000}k/{cm.name:<5} "
                  f"IS {m_is.pnl:>8.0f}  OOS {m_oos.pnl:>8.0f}  "
                  f"S14 {m_s14.pnl:>8.0f}  S22 {m_s22.pnl:>8.0f}  "
                  f"ratio {ratio:>5.2f}  cyc/d {m_oos.trades / days_oos:>5.2f}  "
                  f"({_time.time() - t0:.0f}s)", flush=True)

    fcsv.close()
    print(f"\nCSV: {OUT_CSV.relative_to(ROOT)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
