"""FÁZA 2b — S7 „Continuation“: vstup V SMERE pohybu na dne pullbacku.

Logika z Fázy 1: 65–67 % kvalifikovaných pohybov po pullbacku pokračuje
za pôvodný extrém; pullback má medián ~45 % a dno do ~1 h. S7 kupuje
pokračovanie: vstup pri dne pullbacku, SL tesne pod dnom, TP na extréme.
Oproti S6 (fade) sa RR otáča v prospech obchodu.

Setup (kauzálne):
* noha zigzagu (reverz 0.15 %) sa uzavrie pivotom → kvalifikácia:
  veľkosť ∈ [0.45 %, 0.60 %], trvanie ≤ 24 h, BEZ news baru
  (range ≥ 4× ATR14) počas nohy (news pohyby pokračujú inak — Fáza 1).
* setup {S, E, move} sa odloží bokom a dno pullbacku sa sleduje ďalej
  nezávisle od zigzagu; max 1 setup, max 1 pozícia.

Vstupné triggery (up-pohyb → LONG, zrkadlovo short):
  a) limitka na 45 % retracemente: fill, keď low ≤ E − 0.45×move
  b) dno ≥ 38 % + prvá sviečka späť v smere (close > open)
  c) dno ≥ 38 % + prekonanie high predchádzajúcej sviečky (stop-entry
     na prev_high)

Riadenie: SL = dno_pri_vstupe − 0.05 % (rezerva); TP1 = pôvodný extrém E,
TP2 = E + 0.5× hĺbka pullbacku; časový stop 24 h od vstupu. Zrušenie
setupu: návrat za E pred vstupom (ušlo nám to), pád pod S (plné
otočenie), 24 h od E. Same-bar: po fille sa pripúšťa SL, nie TP
(konzervatívne). Náklady IBKR; TF M5 a H1; pozície 25k/50k.

Stres 2014-15/2021-22 na Duka H1 (M5 variant používa H1 stres ako
proxy). Ak S7 žije z pokračovania, v trendových rokoch MUSÍ zarábať —
to je test diverzifikácie voči gridu.

Beh: python3 strategy_lab_s7.py
Výstup: data/backtest_v2/results_lab_s7.csv + tabuľka.
"""

from __future__ import annotations

import csv
import sys
import time as _time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from backtest_v2 import IBKR_CSV, Bars, atr_wilder, load_dukascopy_h1, \
    load_ibkr_csv, slice_years
from pullback_study import aggregate
from trading.rates import daily_funding_usd

ROOT = Path(__file__).resolve().parent
OUT_CSV = ROOT / "data" / "backtest_v2" / "results_lab_s7.csv"

REV_TH = 0.0015
SIZE_LO, SIZE_HI = 0.0045, 0.0060
MAX_LEG_H = 24.0
SETUP_TTL_H = 24.0
TIME_STOP_H = 24.0
SL_RESERVE = 0.0005
NEWS_ATR_MULT = 4.0
COMM_BPS = 0.2e-4
COMM_MIN = 2.0
HALF_SPREAD = 0.000005
STRESS_FATAL_CAP = 100_000.0


@dataclass
class S7Cfg:
    tf: str = "M5"            # M5 | H1
    trigger: str = "a"        # a=limit45 | b=protisviečka | c=breakout
    tp_mode: str = "E"        # E | ext (E + 0.5× hĺbka)
    qty: float = 25_000
    lvl_a: float = 0.45       # retracement pre limitku (frakcia pohybu)
    lvl_bc: float = 0.38      # minimálna hĺbka pre triggery b/c

    @property
    def vid(self) -> str:
        return (f"S7_{self.tf}_{self.trigger}_tp{self.tp_mode}"
                f"_q{self.qty / 1000:.0f}k")


@dataclass
class S7Metrics:
    pnl: float = 0.0
    gross_win: float = 0.0
    costs: float = 0.0
    trades: int = 0
    wins: int = 0
    timeouts: int = 0
    setups: int = 0
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


def run_s7(b: Bars, cfg: S7Cfg) -> S7Metrics:
    h, l, o, c, t = b.high, b.low, b.open, b.close, b.t
    atr = atr_wilder(b)
    n = len(t)
    m = S7Metrics()
    realized = funding = 0.0
    comm = spread = 0.0
    peak, peak_t = 0.0, None
    holds: list[float] = []
    pos = None            # (side, entry, sl, tp, t0)
    setup = None          # (d, S, E, move_abs, t_E, bottom, armed_bc)
    prev_day = ""

    # zigzag stav + news flag aktuálnej nohy
    hi_p, hi_i = h[0], 0
    lo_p, lo_i = l[0], 0
    direction = 0
    start_p, start_i = c[0], 0
    ext_p, ext_i = c[0], 0
    leg_news = False

    def fill_cost(q: float, price: float) -> float:
        nonlocal comm, spread
        cu = max(COMM_MIN, q * price * COMM_BPS)
        su = q * HALF_SPREAD
        comm += cu / price
        spread += su / price
        return (cu + su) / price

    def close_pos(price: float, tt: float, win: bool, timeout=False):
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

    def try_enter(i: int) -> None:
        nonlocal pos, setup
        d, S, E, move, t_E, bottom, _armed = setup
        depth = (E - bottom) if d == 1 else (bottom - E)
        entry = None
        if cfg.trigger == "a":
            lvl = E - cfg.lvl_a * move if d == 1 else E + cfg.lvl_a * move
            if (d == 1 and l[i] <= lvl) or (d == -1 and h[i] >= lvl):
                entry = lvl
                bottom = min(bottom, lvl) if d == 1 else max(bottom, lvl)
                depth = (E - bottom) if d == 1 else (bottom - E)
        elif depth >= cfg.lvl_bc * move:
            if cfg.trigger == "b":
                if (d == 1 and c[i] > o[i]) or (d == -1 and c[i] < o[i]):
                    entry = c[i]
            else:                                  # c: breakout prev bar
                if i > 0:
                    if d == 1 and h[i] > h[i - 1]:
                        entry = max(h[i - 1], o[i])
                    elif d == -1 and l[i] < l[i - 1]:
                        entry = min(l[i - 1], o[i])
        if entry is None:
            return
        side = "long" if d == 1 else "short"
        rez = SL_RESERVE * E
        sl_p = bottom - rez if d == 1 else bottom + rez
        if cfg.tp_mode == "E":
            tp_p = E
        else:
            tp_p = E + 0.5 * depth if d == 1 else E - 0.5 * depth
        if (d == 1 and (entry <= sl_p or tp_p <= entry)) or \
           (d == -1 and (entry >= sl_p or tp_p >= entry)):
            return
        nonlocal_cost = fill_cost(cfg.qty, entry)
        nonlocal realized
        realized -= nonlocal_cost
        pos = (side, entry, sl_p, tp_p, t[i])
        setup = None
        # same-bar SL (konzervatívne; TP same-bar nie)
        if (d == 1 and l[i] <= sl_p) or (d == -1 and h[i] >= sl_p):
            close_pos(sl_p, t[i], False)

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
                side, e, *_r, t0 = pos[0], pos[1], pos[2], pos[3], pos[4]
                win = (c[i] > pos[1]) if pos[0] == "long" else (c[i] < pos[1])
                close_pos(c[i], t[i], win, timeout=True)

        # --- setup: update dna, cancel, vstup ---------------------------------
        if setup is not None:
            d, S, E, move, t_E, bottom, armed = setup
            bottom = min(bottom, l[i]) if d == 1 else max(bottom, h[i])
            setup = (d, S, E, move, t_E, bottom, armed)
            cancel = ((t[i] - t_E) / 3600 > SETUP_TTL_H
                      or (d == 1 and (h[i] > E or l[i] < S))
                      or (d == -1 and (l[i] < E or h[i] > S)))
            if pos is None:
                try_enter(i)
            if setup is not None and cancel:
                setup = None

        # --- zigzag ------------------------------------------------------------
        if not np.isnan(atr[i]) and (h[i] - l[i]) >= NEWS_ATR_MULT * atr[i]:
            leg_news = True
        pivot_dir = 0
        if direction == 0:
            if h[i] > hi_p:
                hi_p, hi_i = h[i], i
            if l[i] < lo_p:
                lo_p, lo_i = l[i], i
            if hi_p - l[i] > hi_p * REV_TH:
                direction = -1
                start_p, start_i = hi_p, hi_i
                ext_p, ext_i = l[i], i
                leg_news = False
            elif h[i] - lo_p > lo_p * REV_TH:
                direction = 1
                start_p, start_i = lo_p, lo_i
                ext_p, ext_i = h[i], i
                leg_news = False
        elif direction == 1:
            if h[i] > ext_p:
                ext_p, ext_i = h[i], i
            if ext_p - l[i] > ext_p * REV_TH:
                pivot_dir = 1
        else:
            if l[i] < ext_p:
                ext_p, ext_i = l[i], i
            if h[i] - ext_p > ext_p * REV_TH:
                pivot_dir = -1

        if pivot_dir != 0:
            move = abs(ext_p - start_p)
            size = move / start_p
            dur_h = (t[ext_i] - t[start_i]) / 3600
            if (setup is None and pos is None
                    and SIZE_LO <= size <= SIZE_HI
                    and dur_h <= MAX_LEG_H and not leg_news):
                bottom0 = l[i] if pivot_dir == 1 else h[i]
                setup = (pivot_dir, start_p, ext_p, move, t[ext_i],
                         bottom0, False)
                m.setups += 1
                if pos is None:
                    try_enter(i)
            # flip nohy
            if pivot_dir == 1:
                start_p, start_i = ext_p, ext_i
                direction = -1
                ext_p, ext_i = l[i], i
            else:
                start_p, start_i = ext_p, ext_i
                direction = 1
                ext_p, ext_i = h[i], i
            leg_news = False

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


CSV_COLS = ["variant", "tf", "trigger", "tp_mode", "qty",
            "pnl_is", "pnl_oos", "pnl_s14", "pnl_s22",
            "dd_oos", "ratio_oos", "cost_ratio_oos", "setups_oos",
            "trades_oos", "win_oos_pct", "timeouts_oos", "avg_hold_h_oos",
            "min_cap_oos", "underwater_days_oos",
            "pnl_oos_p08", "pnl_oos_p12", "flags"]


def main() -> int:
    t0 = _time.time()
    print("S7 Continuation LAB — pripravujem dáta…", flush=True)
    ibkr = load_ibkr_csv(IBKR_CSV)
    duka = load_dukascopy_h1()

    sets: dict[str, dict[str, Bars]] = {}
    for tf in ("M5", "H1"):
        fac = 1 if tf == "M5" else 12
        mk = (lambda bb, nm: bb) if fac == 1 else \
            (lambda bb, nm: aggregate(bb, fac, nm))
        sets[tf] = {
            "is": mk(slice_years(ibkr, "IS", 2023, 2024), "IS"),
            "oos": mk(slice_years(ibkr, "OOS", 2025, 2026), "OOS"),
            "s14": slice_years(duka, "S14", 2014, 2015),
            "s22": slice_years(duka, "S22", 2021, 2022),
        }

    variants = [S7Cfg(tf, tr, tp, q)
                for tf in ("M5", "H1")
                for tr in ("a", "b", "c")
                for tp in ("E", "ext")
                for q in (25_000, 50_000)]

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    f = open(OUT_CSV, "w", newline="")
    wr = csv.writer(f)
    wr.writerow(CSV_COLS)
    rows = []
    yrs_is, yrs_oos = 1.35, 1.55

    for k, cfg in enumerate(variants, 1):
        d = sets[cfg.tf]
        m_is = run_s7(d["is"], cfg)
        m_oos = run_s7(d["oos"], cfg)
        m_s14 = run_s7(d["s14"], cfg)
        m_s22 = run_s7(d["s22"], cfg)
        p08 = p12 = float("nan")
        if m_oos.pnl > 0:
            key = "lvl_a" if cfg.trigger == "a" else "lvl_bc"
            p08 = run_s7(d["oos"], replace(cfg, **{key: getattr(cfg, key) * 0.8})).pnl
            p12 = run_s7(d["oos"], replace(cfg, **{key: getattr(cfg, key) * 1.2})).pnl
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
        row = [cfg.vid, cfg.tf, cfg.trigger, cfg.tp_mode, int(cfg.qty),
               round(m_is.pnl), round(m_oos.pnl), round(m_s14.pnl),
               round(m_s22.pnl), round(m_oos.max_dd), round(ratio, 3),
               round(m_oos.cost_ratio, 3), m_oos.setups, m_oos.trades,
               round(m_oos.win_rate, 1), m_oos.timeouts,
               round(m_oos.avg_hold_h, 1), round(m_oos.min_cap),
               round(m_oos.underwater_days, 1),
               round(p08) if not np.isnan(p08) else "",
               round(p12) if not np.isnan(p12) else "",
               "|".join(flags)]
        rows.append((ratio, row))
        wr.writerow(row)
        f.flush()
        print(f"[{k}/{len(variants)}] {cfg.vid:<22} IS {m_is.pnl:>7.0f}  "
              f"OOS {m_oos.pnl:>7.0f}  S14 {m_s14.pnl:>6.0f}  "
              f"S22 {m_s22.pnl:>6.0f}  n={m_oos.trades:<4} "
              f"win {m_oos.win_rate:>4.1f}%  "
              f"[{','.join(flags) or 'ok'}]  ({_time.time() - t0:.0f}s)",
              flush=True)
    f.close()

    print("\n=== TOP 10 podľa P/L / maxDD (OOS) ===")
    hdr = (f"{'variant':<22}{'OOS':>8}{'DD':>7}{'ratio':>7}{'IS':>8}"
           f"{'S14':>7}{'S22':>7}{'n':>6}{'win%':>6}{'cost%':>7}  flagy")
    print(hdr)
    print("-" * len(hdr))
    for ratio, r in sorted(rows, key=lambda x: -x[0])[:10]:
        print(f"{r[0]:<22}{r[6]:>8}{r[9]:>7}{ratio:>7.2f}{r[5]:>8}"
              f"{r[7]:>7}{r[8]:>7}{r[13]:>6}{r[14]:>6}{100 * r[11]:>6.1f}%  {r[21]}")
    print(f"\nCSV: {OUT_CSV.relative_to(ROOT)} ({(_time.time() - t0) / 60:.1f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
