"""G2B TP MATICA — sweep TP per smer na fixnej G2B geometrii.

Geometria (fixná): krok short +0.15 %, long −0.225 % + 2×ATR filter,
pozícia 10k, pásma 1.12/1.16, kapacita 20+10, gap B (TP na preskočenej
úrovni — gap TP ostáva na kroku, sweep mení len ne-gap TP), news pauza
1 h po spike bare (kauzálna proxy blackoutu ±30 min).

Matica: short TP {0.06, 0.08, 0.10, 0.20, 0.30 %} × long TP {0.06, 0.08,
0.10 %} = 15 kombinácií vrátane asymetrických. Baseline = 0.10/0.10.

Náklady: Razor model. Funding z historickej tabuľky Fed−ECB PO OPRAVE
flooru (30. 7. 2026): short platí (diff − 1 %) aj keď je záporné —
v 2013–15 a 2020–22 shorty swap PLATILI (~0.25–0.75 % p.a.), čo je
podstatné práve pre dlho visiace shorty s TP 0.20/0.30 %.

Okná IS/OOS/S14/S22, kill pravidlá hlavného labu (FRAGILE sa nehodnotí —
matica je sweep). Výstupy navyše: cykly/deň, priemerné visenie per smer,
funding per smer, kapitálová potreba 1.5×(margin plnej kapacity + najhorší
floating).

Pravidlo po S7: prípadný víťaz nad baseline sa pred verdiktom overuje
nezávislou reimplementáciou (samostatný skript, iná štruktúra výpočtu).

Beh: python3 strategy_lab_g2b_tpmatrix.py
Výstup: data/backtest_v2/results_g2b_tpmatrix.csv + tabuľka na stdout.
"""

from __future__ import annotations

import csv
import sys
import time as _time
from pathlib import Path

from backtest_v2 import load_dukascopy_h1, load_ibkr_csv, slice_years, IBKR_CSV
from strategy_lab import build_daily_ctx, prepare, STRESS_FATAL_CAP
from strategy_lab_g2b5k import FineCfg, RAZOR, run_fine, cap_need

ROOT = Path(__file__).resolve().parent
OUT_CSV = ROOT / "data" / "backtest_v2" / "results_g2b_tpmatrix.csv"

SHORT_TPS = (0.0006, 0.0008, 0.0010, 0.0020, 0.0030)
LONG_TPS = (0.0006, 0.0008, 0.0010)

CSV_COLS = [
    "variant", "tp_short_bp", "tp_long_bp",
    "pnl_is", "pnl_oos", "pnl_s14", "pnl_s22",
    "dd_is", "dd_oos", "ratio_oos", "cost_ratio_oos",
    "trades_oos", "cyc_day_oos", "win_oos_pct",
    "hold_h_long_oos", "hold_h_short_oos",
    "fund_long_oos", "fund_short_oos", "fund_short_s14", "fund_short_s22",
    "uw_days_oos", "kap_norm", "kap_s14", "kap_s22",
    "mincap_s22", "open_end_oos", "flags",
]


def main() -> int:
    t0 = _time.time()
    print("G2B TP MATICA — načítavam dáta…", flush=True)
    ibkr = load_ibkr_csv(IBKR_CSV)
    duka = load_dukascopy_h1()
    dctx = build_daily_ctx(duka, ibkr)

    ds_is = prepare("IBKR_IS_2023-24", slice_years(ibkr, "IS", 2023, 2024), dctx, 3)
    ds_oos = prepare("IBKR_OOS_2025-26", slice_years(ibkr, "OOS", 2025, 2026), dctx, 3)
    ds_s14 = prepare("STRES_2014-15", slice_years(duka, "S14", 2014, 2015), dctx, 1)
    ds_s22 = prepare("STRES_2021-22", slice_years(duka, "S22", 2021, 2022), dctx, 1)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fcsv = open(OUT_CSV, "w", newline="")
    writer = csv.writer(fcsv)
    writer.writerow(CSV_COLS)

    rows = []
    combos = [(ts, tl) for ts in SHORT_TPS for tl in LONG_TPS]
    for k, (ts, tl) in enumerate(combos, 1):
        vid = f"TP_s{ts * 1e4:g}bp_l{tl * 1e4:g}bp"
        cfg = FineCfg(qty=10_000, step_s=0.0015, tp=ts, tp_mode="fix",
                      tp_long=tl, news_pause_s=3600.0)
        m_is = run_fine(ds_is, dctx, cfg, RAZOR, False)
        m_oos = run_fine(ds_oos, dctx, cfg, RAZOR, False)
        m_s14 = run_fine(ds_s14, dctx, cfg, RAZOR, False)
        m_s22 = run_fine(ds_s22, dctx, cfg, RAZOR, False)

        flags = []
        if m_oos.cost_ratio > 0.25:
            flags.append("COST_FAIL")
        if m_is.pnl > 0 and m_oos.pnl / ds_oos.years < 0.5 * m_is.pnl / ds_is.years:
            flags.append("OVERFIT")
        if m_is.pnl <= 0:
            flags.append("IS_NEGATIVE")
        if max(m_s14.min_cap, m_s22.min_cap) > STRESS_FATAL_CAP:
            flags.append("STRESS_FATAL")

        ratio = m_oos.pnl / m_oos.max_dd if m_oos.max_dd > 0 else 0.0
        days_oos = ds_oos.years * 365.25
        kap_norm = max(cap_need(cfg, m_is, use_observed_expo=False),
                       cap_need(cfg, m_oos, use_observed_expo=False))
        hold_l = (m_oos.hold_sum_long / m_oos.closes_long / 3600
                  if m_oos.closes_long else 0.0)
        hold_s = (m_oos.hold_sum_short / m_oos.closes_short / 3600
                  if m_oos.closes_short else 0.0)

        row = [vid, round(ts * 1e4, 1), round(tl * 1e4, 1),
               round(m_is.pnl), round(m_oos.pnl),
               round(m_s14.pnl), round(m_s22.pnl),
               round(m_is.max_dd), round(m_oos.max_dd),
               round(ratio, 3), round(m_oos.cost_ratio, 3),
               m_oos.trades, round(m_oos.trades / days_oos, 2),
               round(100 * m_oos.wins / m_oos.trades, 1) if m_oos.trades else 0,
               round(hold_l, 1), round(hold_s, 1),
               round(m_oos.funding_long), round(m_oos.funding_short),
               round(m_s14.funding_short), round(m_s22.funding_short),
               round(m_oos.underwater_days, 1),
               round(kap_norm),
               round(cap_need(cfg, m_s14, use_observed_expo=False)),
               round(cap_need(cfg, m_s22, use_observed_expo=False)),
               round(m_s22.min_cap), m_oos.open_end, "|".join(flags)]
        writer.writerow(row)
        fcsv.flush()
        rows.append(row)
        print(f"[{k}/15] {vid:<18} IS {m_is.pnl:>7.0f}  OOS {m_oos.pnl:>7.0f}  "
              f"S14 {m_s14.pnl:>7.0f}  S22 {m_s22.pnl:>7.0f}  "
              f"ratio {ratio:>5.2f}  hold S {hold_s:>5.1f}h  "
              f"fundS22 {m_s22.funding_short:>6.0f}  "
              f"[{','.join(flags) or 'ok'}]  ({_time.time() - t0:.0f}s)",
              flush=True)

    fcsv.close()
    print("\n=== MATICA OOS P/L (riadky short TP, stĺpce long TP) ===")
    print(f"{'':>8}" + "".join(f"L{tl * 1e4:>5.0f}bp" for tl in LONG_TPS))
    for ts in SHORT_TPS:
        line = f"S{ts * 1e4:>5.0f}bp "
        for tl in LONG_TPS:
            r = next(r for r in rows if r[1] == round(ts * 1e4, 1)
                     and r[2] == round(tl * 1e4, 1))
            line += f"{r[4]:>8}"
        print(line)
    print(f"\nCSV: {OUT_CSV.relative_to(ROOT)}  "
          f"({(_time.time() - t0) / 60:.1f} min)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
