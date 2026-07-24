"""STRATEGY LAB — G9 „ScaledGrid“: grid s košovým zatváraním pri hĺbke.

Základ = G2B (krok 0.15 %, pásma 1.12/1.16, gap TP-skip, kapacita 20+10
s ATR odomykaním rezerv). Nová mechanika pri hĺbke:

* Keď na jednej strane visia ≥ 3 pozície, každá ďalšia úroveň vstupuje
  so škálovaným objemom a tvorí KÔŠ s najbližšou predchádzajúcou voľnou
  (nekošovanou) pozíciou. Kôš sa zavrie, keď spoločný P/L ≥ cieľ
  T = tgt × (súčet individuálnych TP cieľov oboch pozícií).
  Spoločný P/L je lineárny v cene ⇒ kôš má exaktnú spúšťaciu cenu
  btp = (T + e₁q₁ + e₂q₂) / (q₁ + q₂), kontroluje sa na high/low baru.
* Cieľové varianty: tgt ∈ {1.0, 0.8} — či rýchlejšie odblokovanie stojí
  za nižší zisk.
* Škálovacie varianty (reset pri vyprázdnení strany):
    a) 2× len prvá hlboká úroveň, ďalšie opäť 1×
    b) 2× každá hlboká úroveň
    c) progresívne 2×, 3×, 4×… s TVRDÝM stropom expozície strany
       (Σ objemových jednotiek ≤ kapacita = 30 × qty)

Nákladové modely zodpovedajú flotile: 25k verzia = IBKR (0.2 bps min $2,
spread 0.1 pipu), 2k verzia = ECN/cTrader (3.5 USD/100k/strana, spread
0.15/0.5 pipu podľa času+news proxy, slippage 0.2 pipu na market fily).
2k na IBKR sa nebeží — známy COST_FAIL (min. provízia).

Jadro trade-offu sa meria explicitne: priemerný čas visenia pozícií
(vrátane členov košov) a chvostová expozícia (max + p95 z per-bar
vzorkovania) — oboje vs. G2B baseline v rovnakom nákladovom modeli.

Beh: python3 strategy_lab_g9.py
Výstup: data/backtest_v2/results_lab_g9.csv + porovnávacie tabuľky.
"""

from __future__ import annotations

import csv
import sys
import time as _time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import strategy_lab as sl
from backtest_v2 import IBKR_CSV, load_dukascopy_h1, load_ibkr_csv, slice_years
from trading.rates import daily_funding_usd

ROOT = Path(__file__).resolve().parent
OUT_CSV = ROOT / "data" / "backtest_v2" / "results_lab_g9.csv"

# --- nákladové modely --------------------------------------------------------
IBKR_COMM_BPS = 0.2e-4
IBKR_COMM_MIN = 2.0
IBKR_HALF_SPREAD = 0.000005          # 0.05 pipu na fill

ECN_COMM_PER_100K = 3.5
ECN_SPREAD_NORMAL = 0.15e-4
ECN_SPREAD_WIDE = 0.5e-4
ECN_SLIP = 0.2e-4                    # market fily (vstupy); TP limitky nie
ROLLOVER_UTC = (21, 22)
NEWS_RANGE_MULT = 4.0

STRESS_FATAL_CAP = 100_000.0


@dataclass
class G9Cfg:
    qty: float = 25_000
    cost_model: str = "ibkr"         # ibkr | ecn
    scaling: str = "none"            # none (G2B baseline) | a | b | c
    basket_target: float = 1.0       # 1.0 | 0.8 × súčet individuálnych cieľov
    step_s: float = 0.0015
    step_l_ratio: float = 1.5
    tp: float = 0.001
    cap_base: int = 20
    cap_reserve: int = 10
    atr_mult_long: float = 2.0
    band_lo: float = 1.1200
    band_hi: float = 1.1600
    depth_threshold: int = 3         # ≥ 3 visiace pozície ⇒ hlboký režim

    @property
    def cap(self) -> int:
        return self.cap_base + self.cap_reserve

    @property
    def vid(self) -> str:
        base = "G2B_base" if self.scaling == "none" else \
            f"G9{self.scaling}_t{self.basket_target * 100:.0f}"
        return f"{base}_q{self.qty / 1000:.0f}k"


@dataclass
class G9Metrics:
    pnl: float = 0.0
    gross_win: float = 0.0
    costs: float = 0.0
    cycles: int = 0                  # zavreté pozície (členovia koša = 2)
    basket_closes: int = 0
    opened: int = 0
    open_end: int = 0
    max_dd: float = 0.0
    min_cap: float = 0.0
    max_expo: float = 0.0
    p95_expo: float = 0.0
    underwater_days: float = 0.0
    avg_hold_h: float = 0.0          # priemerný čas visenia zavretých pozícií
    p95_hold_h: float = 0.0
    blocked_by_cap: int = 0          # variant c: vstupy zamietnuté stropom

    @property
    def cost_ratio(self) -> float:
        return self.costs / self.gross_win if self.gross_win > 0 else 99.0


def run_g9(p: sl.Prepared, dctx: sl.DailyCtx, cfg: G9Cfg) -> G9Metrics:
    b, atr = p.bars, p.atr
    m = G9Metrics()
    realized = funding = 0.0
    comm = spread_c = slip_c = 0.0
    step_l = cfg.step_s * cfg.step_l_ratio

    # singles: [e, q, tp, t0]; baskets: [e1,q1,e2,q2,btp,target,t01,t02]
    singles = {"long": [], "short": []}
    baskets = {"long": [], "short": []}
    deep_seq = {"long": 0, "short": 0}     # poradie hlbokých vstupov (a/c)
    ref = {"long": b.close[0], "short": b.close[0]}
    last = {"long": 0.0, "short": 0.0}
    holds: list[float] = []
    expo_series = np.empty(len(b.t), dtype=np.float32)
    prev_day = -1
    peak, peak_t = 0.0, None

    def side_count(s: str) -> int:
        return len(singles[s]) + 2 * len(baskets[s])

    def side_units(s: str) -> float:
        u = sum(q for _, q, _, _ in singles[s])
        u += sum(q1 + q2 for _, q1, _, q2, _, _, _, _ in baskets[s])
        return u / cfg.qty

    def fill_cost(q: float, price: float, *, market: bool, wide: bool) -> float:
        nonlocal comm, spread_c, slip_c
        if cfg.cost_model == "ibkr":
            c_usd = max(IBKR_COMM_MIN, q * price * IBKR_COMM_BPS)
            s_usd = q * IBKR_HALF_SPREAD
            sl_usd = 0.0
        else:
            c_usd = q / 100_000 * ECN_COMM_PER_100K
            s_usd = q * (ECN_SPREAD_WIDE if wide else ECN_SPREAD_NORMAL) / 2
            sl_usd = q * ECN_SLIP if market else 0.0
        comm += c_usd / price
        spread_c += s_usd / price
        slip_c += sl_usd / price
        return (c_usd + s_usd + sl_usd) / price

    def close_single(s: str, pos, price: float, t: float, wide: bool) -> None:
        nonlocal realized
        e, q, tp, t0 = pos
        gross = (price - e) * q / price if s == "long" else (e - price) * q / price
        m.gross_win += max(gross, 0.0)
        realized += gross - fill_cost(q, price, market=False, wide=wide)
        m.cycles += 1
        holds.append(t - t0)

    def close_basket(s: str, bk, t: float, wide: bool) -> None:
        nonlocal realized
        e1, q1, e2, q2, btp, target, t01, t02 = bk
        # spoločný P/L pri btp = presne target (konštrukcia btp)
        m.gross_win += max(target, 0.0)
        cost = fill_cost(q1, btp, market=False, wide=wide) \
            + fill_cost(q2, btp, market=False, wide=wide)
        realized += target / btp - cost      # target je v cenových USD → /btp na EUR
        m.cycles += 2
        m.basket_closes += 1
        holds.append(t - t01)
        holds.append(t - t02)

    for i in range(len(b.t)):
        t, h, l, c = b.t[i], b.high[i], b.low[i], b.close[i]
        a = atr[i]
        di = p.day_i[i]
        hour_utc = int(t // 3600) % 24
        wide = (cfg.cost_model == "ecn"
                and (hour_utc in ROLLOVER_UTC
                     or (not np.isnan(a) and (h - l) >= NEWS_RANGE_MULT * a)))

        # funding pri zmene dňa
        if di != prev_day and prev_day >= 0 and di >= 0:
            day = dctx.days[di]
            nd = max(di - prev_day, 1) if p.name in ("IS", "OOS") else 1
            for s in ("long", "short"):
                units = sum(q for _, q, _, _ in singles[s]) + \
                    sum(q1 + q2 for _, q1, _, q2, _, _, _, _ in baskets[s])
                if units:
                    funding += daily_funding_usd(day, s, units, c) * nd / c
        if di >= 0:
            prev_day = di

        # --- výstupy ---------------------------------------------------------
        for s, hit in (("long", h), ("short", l)):
            keep = []
            for pos in singles[s]:
                tp = pos[2]
                if (s == "long" and hit >= tp) or (s == "short" and hit <= tp):
                    close_single(s, pos, tp, t, wide)
                else:
                    keep.append(pos)
            singles[s] = keep
            keepb = []
            for bk in baskets[s]:
                btp = bk[4]
                if (s == "long" and h >= btp) or (s == "short" and l <= btp):
                    close_basket(s, bk, t, wide)
                else:
                    keepb.append(bk)
            baskets[s] = keepb
            if side_count(s) == 0:
                ref[s] = c
                deep_seq[s] = 0

        ref["long"] = max(ref["long"], h)
        ref["short"] = min(ref["short"], l)

        # --- vstupy ----------------------------------------------------------
        if not np.isnan(a):
            for s in ("long", "short"):
                if s == "long":
                    if c >= cfg.band_hi or side_count(s) >= cfg.cap:
                        continue
                    move = ref[s] - c
                    trigger = max(ref[s] * step_l, cfg.atr_mult_long * a)
                    step_abs = ref[s] * step_l
                else:
                    if c <= cfg.band_lo or side_count(s) >= cfg.cap:
                        continue
                    move = c - ref[s]
                    trigger = ref[s] * cfg.step_s
                    step_abs = ref[s] * cfg.step_s
                unlock = (side_count(s) < cfg.cap_base
                          or abs(c - last[s]) > 2.0 * a)
                if move < trigger or not unlock:
                    continue

                k = int(move / step_abs) if step_abs > 0 else 1
                gap = k >= 2
                step_pct = step_l if s == "long" else cfg.step_s
                if s == "long":
                    tp = c * (1 + (step_pct if gap else cfg.tp))
                else:
                    tp = c * (1 - (step_pct if gap else cfg.tp))

                deep = (cfg.scaling != "none"
                        and side_count(s) >= cfg.depth_threshold)
                mult = 1.0
                if deep:
                    deep_seq[s] += 1
                    if cfg.scaling == "a":
                        mult = 2.0 if deep_seq[s] == 1 else 1.0
                    elif cfg.scaling == "b":
                        mult = 2.0
                    else:                              # c: 2×, 3×, 4×…
                        mult = float(deep_seq[s] + 1)
                        if side_units(s) + mult > cfg.cap:   # tvrdý strop
                            m.blocked_by_cap += 1
                            deep_seq[s] -= 1
                            continue
                q = cfg.qty * mult
                realized -= fill_cost(q, c, market=True, wide=wide)
                m.opened += 1
                last[s] = c
                ref[s] = c

                partner = singles[s].pop() if (deep and singles[s]) else None
                if partner is not None:
                    e1, q1, tp1, t01 = partner
                    tgt1 = abs(tp1 - e1) * q1
                    tgt2 = abs(tp - c) * q
                    target = cfg.basket_target * (tgt1 + tgt2)
                    if s == "long":
                        btp = (target + e1 * q1 + c * q) / (q1 + q)
                    else:
                        btp = (e1 * q1 + c * q - target) / (q1 + q)
                    baskets[s].append([e1, q1, c, q, btp, target, t01, t])
                else:
                    singles[s].append([c, q, tp, t])

        # --- equity ----------------------------------------------------------
        float_usd = 0.0
        expo = 0.0
        for s, sign in (("long", 1.0), ("short", -1.0)):
            for e, q, _tp, _t0 in singles[s]:
                float_usd += sign * (c - e) * q
                expo += q
            for e1, q1, e2, q2, _btp, _tg, _t1, _t2 in baskets[s]:
                float_usd += sign * ((c - e1) * q1 + (c - e2) * q2)
                expo += q1 + q2
        eq = realized + funding + float_usd / c
        expo_series[i] = expo
        m.max_expo = max(m.max_expo, expo)
        if peak_t is None or eq > peak:
            if peak_t is not None:
                m.underwater_days = max(m.underwater_days, (t - peak_t) / 86400)
            peak, peak_t = eq, t
        m.max_dd = max(m.max_dd, peak - eq)
        m.min_cap = max(m.min_cap, expo / 30.0 - eq)

    if peak_t is not None:
        m.underwater_days = max(m.underwater_days,
                                (b.t[-1] - peak_t) / 86400)
    c_end = b.close[-1]
    float_usd = 0.0
    open_n = 0
    for s, sign in (("long", 1.0), ("short", -1.0)):
        for e, q, _tp, _t0 in singles[s]:
            float_usd += sign * (c_end - e) * q
            open_n += 1
        for e1, q1, e2, q2, *_ in baskets[s]:
            float_usd += sign * ((c_end - e1) * q1 + (c_end - e2) * q2)
            open_n += 2
    m.pnl = realized + funding + float_usd / c_end
    m.open_end = open_n
    m.costs = comm + spread_c + slip_c + max(-funding, 0.0)
    m.min_cap = max(m.min_cap, 0.0)
    m.p95_expo = float(np.percentile(expo_series, 95)) if len(b.t) else 0.0
    if holds:
        harr = np.asarray(holds) / 3600.0
        m.avg_hold_h = float(harr.mean())
        m.p95_hold_h = float(np.percentile(harr, 95))
    return m


# ===========================================================================

def build_variants() -> list[G9Cfg]:
    out = []
    for qty, model in ((25_000, "ibkr"), (2_000, "ecn")):
        out.append(G9Cfg(qty=qty, cost_model=model, scaling="none"))
        for scal in ("a", "b", "c"):
            for tgt in (1.0, 0.8):
                out.append(G9Cfg(qty=qty, cost_model=model,
                                 scaling=scal, basket_target=tgt))
    return out


CSV_COLS = [
    "variant", "qty", "cost_model", "scaling", "basket_target",
    "pnl_is", "pnl_oos", "pnl_s14", "pnl_s22",
    "dd_oos", "ratio_oos", "cost_ratio_oos", "cycles_oos", "baskets_oos",
    "max_expo_oos", "p95_expo_oos", "min_cap_oos", "min_cap_s14",
    "min_cap_s22", "underwater_days_oos",
    "avg_hold_h_oos", "p95_hold_h_oos", "blocked_by_cap_oos",
    "pnl_oos_p08", "pnl_oos_p12", "flags",
]


def main() -> int:
    t0 = _time.time()
    print("G9 ScaledGrid LAB — načítavam dáta…", flush=True)
    ibkr = load_ibkr_csv(IBKR_CSV)
    duka = load_dukascopy_h1()
    dctx = sl.build_daily_ctx(duka, ibkr)
    ds_is = sl.prepare("IS", slice_years(ibkr, "IS", 2023, 2024), dctx, 3)
    ds_oos = sl.prepare("OOS", slice_years(ibkr, "OOS", 2025, 2026), dctx, 3)
    ds_s14 = sl.prepare("S14", slice_years(duka, "S14", 2014, 2015), dctx, 1)
    ds_s22 = sl.prepare("S22", slice_years(duka, "S22", 2021, 2022), dctx, 1)

    variants = build_variants()
    print(f"Variantov: {len(variants)} (2 baseline + 12 G9)", flush=True)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    f = open(OUT_CSV, "w", newline="")
    wr = csv.writer(f)
    wr.writerow(CSV_COLS)

    results: dict[str, dict] = {}
    for k, cfg in enumerate(variants, 1):
        m_is = run_g9(ds_is, dctx, cfg)
        m_oos = run_g9(ds_oos, dctx, cfg)
        m_s14 = run_g9(ds_s14, dctx, cfg)
        m_s22 = run_g9(ds_s22, dctx, cfg)

        p08 = p12 = float("nan")
        if m_oos.pnl > 0 and cfg.scaling != "none":
            p08 = run_g9(ds_oos, dctx, replace(cfg, step_s=cfg.step_s * 0.8)).pnl
            p12 = run_g9(ds_oos, dctx, replace(cfg, step_s=cfg.step_s * 1.2)).pnl

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
        results[cfg.vid] = {"cfg": cfg, "is": m_is, "oos": m_oos,
                            "s14": m_s14, "s22": m_s22, "ratio": ratio,
                            "flags": flags}
        wr.writerow([cfg.vid, int(cfg.qty), cfg.cost_model, cfg.scaling,
                     cfg.basket_target,
                     round(m_is.pnl), round(m_oos.pnl), round(m_s14.pnl),
                     round(m_s22.pnl), round(m_oos.max_dd), round(ratio, 3),
                     round(m_oos.cost_ratio, 3), m_oos.cycles,
                     m_oos.basket_closes, round(m_oos.max_expo),
                     round(m_oos.p95_expo), round(m_oos.min_cap),
                     round(m_s14.min_cap), round(m_s22.min_cap),
                     round(m_oos.underwater_days, 1),
                     round(m_oos.avg_hold_h, 2), round(m_oos.p95_hold_h, 1),
                     m_oos.blocked_by_cap,
                     round(p08) if not np.isnan(p08) else "",
                     round(p12) if not np.isnan(p12) else "",
                     "|".join(flags)])
        f.flush()
        print(f"[{k}/{len(variants)}] {cfg.vid:<20} "
              f"IS {m_is.pnl:>7.0f}  OOS {m_oos.pnl:>7.0f}  "
              f"S14 {m_s14.pnl:>7.0f}  S22 {m_s22.pnl:>7.0f}  "
              f"hold {m_oos.avg_hold_h:>6.1f}h  expo {m_oos.max_expo:>8.0f}  "
              f"[{','.join(flags) or 'ok'}]  ({_time.time() - t0:.0f}s)",
              flush=True)
    f.close()

    # --- porovnanie vs baseline (jadro trade-offu) -------------------------
    for size, model in (("25k", "ibkr"), ("2k", "ecn")):
        base = results[f"G2B_base_q{size}"]
        bo = base["oos"]
        print(f"\n=== G9 vs G2B baseline — {size} ({model.upper()} náklady, OOS) ===")
        hdr = (f"{'variant':<16}{'P/L':>8}{'ΔP/L%':>8}{'ratio':>7}"
               f"{'hold h':>8}{'Δhold%':>8}{'maxExpo':>9}{'Δexpo%':>8}"
               f"{'p95exp':>8}{'uw dni':>7}{'kapS22':>8}  flagy")
        print(hdr)
        print("-" * len(hdr))
        for vid, r in results.items():
            if not vid.endswith(f"_q{size}"):
                continue
            o = r["oos"]
            dpnl = 100 * (o.pnl - bo.pnl) / abs(bo.pnl) if bo.pnl else 0
            dhold = 100 * (o.avg_hold_h - bo.avg_hold_h) / bo.avg_hold_h \
                if bo.avg_hold_h else 0
            dexpo = 100 * (o.max_expo - bo.max_expo) / bo.max_expo \
                if bo.max_expo else 0
            print(f"{vid.replace(f'_q{size}', ''):<16}{o.pnl:>8.0f}"
                  f"{dpnl:>+8.1f}{r['ratio']:>7.2f}{o.avg_hold_h:>8.1f}"
                  f"{dhold:>+8.1f}{o.max_expo:>9.0f}{dexpo:>+8.1f}"
                  f"{o.p95_expo:>8.0f}{o.underwater_days:>7.1f}"
                  f"{r['s22'].min_cap:>8.0f}  {','.join(r['flags']) or 'ok'}")
    print(f"\nCSV: {OUT_CSV.relative_to(ROOT)} "
          f"({(_time.time() - t0) / 60:.1f} min)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
