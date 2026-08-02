#!/usr/bin/env python3
"""Walk-forward: IS 3 roky, OOS 1 rok, kotúľanie po roku.

Beh je SPOJITÝ — mení sa len parametrová sada, pozície prechádzajú cez
hranice okien. Sekanie behu na ročné okná by gridu vzalo jeho jadro
(držať podvodné úrovne, kým sa cena nevráti) a robilo by z každého 31. 12.
umelú realizáciu straty.

Parametre pre rok Y sa ladia na rokoch Y−3 … Y−1. Baseline, V2 a V3
nemajú čo ladiť; u V1 sa hľadá k.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from engine import Cfg, Rates, Result, load_h1, run

CAPITAL = 100_000.0          # € — unesie plný ladder (30 × 10k na smer)
BARS_PER_YEAR = 6230         # H1: ~6230 barov/rok (overené na dátach)
V1_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
IS_YEARS = 3


def year_of(d: str) -> int:
    return int(d[:4])


def year_index(bars: list) -> dict[int, tuple[int, int]]:
    idx: dict[int, tuple[int, int]] = {}
    for i, b in enumerate(bars):
        y = year_of(b[0])
        idx[y] = (idx.get(y, (i, i))[0], i)
    return idx


def metrics(res: Result, capital: float = CAPITAL) -> dict:
    eq = res.equity
    if not eq:
        return {}
    vals = [capital + v for _, v in eq]
    peak, max_dd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak)

    years = max(len(eq) / BARS_PER_YEAR, 1e-9)
    start, end = vals[0], vals[-1]
    cagr = ((end / start) ** (1 / years) - 1) * 100 if start > 0 and end > 0 else float("nan")

    # najdlhšia séria dní bez ziskovej uzávierky
    # sucho v dňoch: bary agregujeme na dátum, inak by H1 nafúklo číslo 24×
    day_seq, seen = [], set()
    for d, _ in eq:
        day = d[:10]
        if day not in seen:
            seen.add(day)
            day_seq.append(day)
    profit_days = {t["date"][:10] for t in res.closed if t["net"] > 0}
    run_days = best = 0
    for day in day_seq:
        if day in profit_days:
            best = max(best, run_days)
            run_days = 0
        else:
            run_days += 1
    best = max(best, run_days)

    return {
        "cagr": cagr,
        "max_dd": max_dd * 100,
        "dry_days": best,
        "max_float_pct": abs(res.max_float_pct) / capital * 100,
        "full_ladder_pct": res.full_ladder_bars / max(res.bars, 1) * 100,
        "cycles": len(res.closed),
        "net": sum(t["net"] for t in res.closed),
        "gross": sum(t["gross"] for t in res.closed),
        "cost": sum(t["cost"] for t in res.closed),
        "swap": sum(t["swap"] for t in res.closed),
        "final_eq": end - capital,
        "blocked": res.blocked_by_gate,
    }


def survives(bars, cfg, rates, y0: int, y1: int, cfg_at=None) -> tuple[bool, float, float]:
    """Prežije obdobie? Kritérium: účet neklesne pod 50 % kapitálu."""
    idx = year_index(bars)
    if y0 not in idx or y1 not in idx:
        return True, 0.0, 0.0
    r = run(bars, cfg, rates, idx[y0][0], idx[y1][1] + 1, cfg_at=cfg_at)
    vals = [CAPITAL + v for _, v in r.equity]
    if not vals:
        return True, 0.0, 0.0
    peak, dd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak)
    return min(vals) > CAPITAL * 0.5, dd * 100, vals[-1] - CAPITAL


# Počet voľných parametrov, ktoré geometriu skutočne určujú.
PARAMS = {
    "baseline": 8,           # step_s, step_l, tp, band_lo, band_hi, atr_mult, base, reserve
    "baseline_bez_pasiem": 6,
    "V1_atr_krok": 5,        # k, tp, atr_mult, base, reserve
    "V2_median_kotva": 8,    # ...+ median_win, band_rel namiesto 2 absolútnych
    "V3_sadzbovy_gate": 6,   # ako baseline bez pásiem, gate je bezparametrový
    "V1+V2+V3": 7,
}

VARIANTS: dict[str, Cfg] = {
    "baseline":             Cfg(),
    "baseline_bez_pasiem":  Cfg(no_bands=True),
    "V1_atr_krok":          Cfg(v1_atr_step=True, no_bands=True),
    "V2_median_kotva":      Cfg(v2_median_anchor=True),
    "V3_sadzbovy_gate":     Cfg(v3_rate_gate=True, no_bands=True),
    "V1+V2+V3":             Cfg(v1_atr_step=True, v2_median_anchor=True,
                                v3_rate_gate=True),
}


def main() -> None:
    bars = load_h1()
    rates = Rates()
    idx = year_index(bars)
    years = sorted(idx)
    first_oos = years[0] + IS_YEARS
    oos_years = [y for y in years if y >= first_oos]
    oos_start = idx[oos_years[0]][0]

    print(f"barov: {len(bars)}   {bars[0][0]} → {bars[-1][0]}")
    print(f"OOS: {oos_years[0]}–{oos_years[-1]} ({len(oos_years)} rokov), "
          f"IS okno {IS_YEARS} r., beh spojitý\n")

    out: dict[str, dict] = {}
    for name, cfg in VARIANTS.items():
        # --- ladenie k na IS oknách (len V1) ---
        k_by_year: dict[int, float] = {}
        if cfg.v1_atr_step:
            for y in oos_years:
                lo = idx[y - IS_YEARS][0]
                hi = idx[y - 1][1] + 1
                best_k, best = V1_GRID[0], float("-inf")
                for k in V1_GRID:
                    r = run(bars, replace(cfg, v1_k=k), rates, lo, hi)
                    v = sum(t["net"] for t in r.closed)
                    if v > best:
                        best, best_k = v, k
                k_by_year[y] = best_k

        def cfg_at(i: int, _cfg=cfg, _k=k_by_year):
            if not _cfg.v1_atr_step:
                return _cfg
            return replace(_cfg, v1_k=_k.get(year_of(bars[i][0]), 1.0))

        res = run(bars, cfg, rates, oos_start, len(bars), cfg_at=cfg_at)
        m = metrics(res)
        m["params"] = PARAMS[name]
        m["k_range"] = (f"{min(k_by_year.values())}–{max(k_by_year.values())}"
                        if k_by_year else "—")
        s08, dd08, pl08 = survives(bars, cfg, rates, 2014, 2015, cfg_at)
        s22, dd22, pl22 = survives(bars, cfg, rates, 2022, 2022, cfg_at)
        m["surv_1415"] = s08
        m["dd_1415"] = dd08
        m["pl_1415"] = pl08
        m["surv_2022"] = s22
        m["dd_2022"] = dd22
        m["pl_2022"] = pl22
        out[name] = m
        out[name]["_equity"] = res.equity

        print(f"{name:22} CAGR {m['cagr']:+6.2f} %  DD {m['max_dd']:5.1f} %  "
              f"sucho {m['dry_days']:4d} d  float {m['max_float_pct']:5.1f} %  "
              f"ladder {m['full_ladder_pct']:4.1f} %  cyklov {m['cycles']:5d}  "
              f"konečná {m['final_eq']:+8.0f} €")

    Path("results.json").write_text(json.dumps(
        {k: {kk: vv for kk, vv in v.items() if kk != "_equity"}
         for k, v in out.items()}, indent=1, ensure_ascii=False))
    Path("equity.json").write_text(json.dumps(
        {k: v["_equity"] for k, v in out.items()}, ensure_ascii=False))
    print("\nzapísané: results.json, equity.json")


if __name__ == "__main__":
    main()
