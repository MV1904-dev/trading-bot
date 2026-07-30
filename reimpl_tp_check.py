"""NEZÁVISLÁ REIMPLEMENTÁCIA — overenie TP matice (pravidlo po S7).

Prepočíta OOS 2025-26 pre kandidátov TP matice čistou implementáciou
G2B zo špecifikácie: vlastné načítanie CSV, vlastný Wilder ATR, vlastný
funding walk, iné dátové štruktúry (pozície ako dicty, deque spike
okno). Zámerne NEimportuje nič zo strategy_lab* — jediný spoločný bod
sú dáta a sadzbová tabuľka (trading/rates.py, tá JE špecifikácia).

Porovnáva: P/L, počet obchodov, max DD, ratio. Toleranca: obchody
presne, peniaze < 1 € (poradie floatov).

Beh: python3 reimpl_tp_check.py
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timezone

from trading.rates import daily_funding_usd

QTY = 10_000.0
STEP_S = 0.0015
STEP_L = 0.00225
ATR_N = 14
ATR_MULT_LONG = 2.0
BAND_LO, BAND_HI = 1.1200, 1.1600
CAP_BASE, CAP_RESERVE = 20, 10
CAP = CAP_BASE + CAP_RESERVE
NEWS_MULT = 4.0
NEWS_PAUSE = 3600.0
COMM_PER_100K = 3.0
COMM_MIN = 0.04
HALF_SPREAD = 0.075e-4


def load_oos(path: str = "data/ibkr_EURUSD_M5.csv"):
    rows = []
    with open(path) as f:
        for rec in csv.DictReader(f):
            ts = rec.get("date") or rec.get("time") or rec.get("ts")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt.year in (2025, 2026):
                rows.append((dt.timestamp(),
                             float(rec["open"]), float(rec["high"]),
                             float(rec["low"]), float(rec["close"])))
    rows.sort()
    return rows


def simulate(bars, tp_short: float, tp_long: float,
             trace: list | None = None) -> dict:
    # vlastný Wilder ATR
    atr = None
    prev_close = None
    seeds = []

    longs: list[dict] = []
    shorts: list[dict] = []
    ref_l = ref_s = bars[0][4]
    last_l = last_s = 0.0
    realized = 0.0
    gross = costs = funding = 0.0
    trades = 0
    peak = None
    max_dd = 0.0
    spike_block_until = -1.0
    prev_date = None

    def fee(price: float) -> float:
        nonlocal costs
        c_usd = max(COMM_MIN, QTY / 100_000 * COMM_PER_100K)
        s_usd = QTY * HALF_SPREAD
        eur = (c_usd + s_usd) / price
        costs += eur
        return eur

    for t, o, h, l, c in bars:
        # ATR
        if prev_close is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        prev_close = c
        if atr is None:
            seeds.append(tr)
            if len(seeds) == ATR_N:
                atr = sum(seeds) / ATR_N
        else:
            atr = (atr * (ATR_N - 1) + tr) / ATR_N

        # funding pri zmene dátumu (kľúč = dátum baru, cena = close)
        date = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        if prev_date is not None and date != prev_date:
            for p in longs:
                funding += daily_funding_usd(date, "long", QTY, c) / c
            for p in shorts:
                funding += daily_funding_usd(date, "short", QTY, c) / c
        prev_date = date

        # TP exity (limitky na high/low); kotva sa resetuje LEN pri
        # zavretí poslednej pozície strany (inak trailuje max/min)
        if longs:
            still = []
            for p in longs:
                if h >= p["tp"]:
                    g = (p["tp"] - p["e"]) * QTY / p["tp"]
                    gross += max(g, 0.0)
                    realized += g - fee(p["tp"])
                    trades += 1
                else:
                    still.append(p)
            longs = still
            if not longs:
                ref_l = c
        if shorts:
            still = []
            for p in shorts:
                if l <= p["tp"]:
                    g = (p["e"] - p["tp"]) * QTY / p["tp"]
                    gross += max(g, 0.0)
                    realized += g - fee(p["tp"])
                    trades += 1
                else:
                    still.append(p)
            shorts = still
            if not shorts:
                ref_s = c

        ref_l = max(ref_l, h)
        ref_s = min(ref_s, l)

        if atr is not None:
            # news proxy: spike bar blokuje vstupy do t+1h (vrátane seba)
            if h - l >= NEWS_MULT * atr:
                spike_block_until = t + NEWS_PAUSE
            if t >= spike_block_until:
                if c < BAND_HI and len(longs) < CAP:
                    drop = ref_l - c
                    if (drop >= max(ref_l * STEP_L, ATR_MULT_LONG * atr)
                            and (len(longs) < CAP_BASE
                                 or abs(c - last_l) > 2.0 * atr)):
                        k = int(drop / (ref_l * STEP_L))
                        tp = c * (1 + (STEP_L if k >= 2 else tp_long))
                        realized -= fee(c)
                        longs.append({"e": c, "tp": tp})
                        if trace is not None:
                            trace.append((t, "L", round(c, 5), round(tp, 5)))
                        last_l = ref_l = c
                if c > BAND_LO and len(shorts) < CAP:
                    rise = c - ref_s
                    if (rise >= ref_s * STEP_S
                            and (len(shorts) < CAP_BASE
                                 or abs(c - last_s) > 2.0 * atr)):
                        k = int(rise / (ref_s * STEP_S))
                        tp = c * (1 - (STEP_S if k >= 2 else tp_short))
                        realized -= fee(c)
                        shorts.append({"e": c, "tp": tp})
                        if trace is not None:
                            trace.append((t, "S", round(c, 5), round(tp, 5)))
                        last_s = ref_s = c

        fl = (sum((c - p["e"]) for p in longs)
              + sum((p["e"] - c) for p in shorts)) * QTY / c
        eq = realized + fl
        if peak is None or eq > peak:
            peak = eq
        max_dd = max(max_dd, peak - eq)

    c_end = bars[-1][4]
    fl = (sum((c_end - p["e"]) for p in longs)
          + sum((p["e"] - c_end) for p in shorts)) * QTY / c_end
    pnl = realized + fl
    return {"pnl": pnl, "trades": trades, "max_dd": max_dd,
            "ratio": pnl / max_dd if max_dd > 0 else 0.0,
            "open_end": len(longs) + len(shorts), "funding": funding,
            "costs": costs, "gross": gross}


def main() -> int:
    print("REIMPL CHECK — načítavam OOS dáta…", flush=True)
    bars = load_oos()
    print(f"  {len(bars):,} barov 2025-26", flush=True)
    for name, ts, tl in (("baseline s10/l10", 0.0010, 0.0010),
                         ("kandidát s8/l10", 0.0008, 0.0010),
                         ("kandidát s6/l10", 0.0006, 0.0010)):
        r = simulate(bars, ts, tl)
        print(f"{name:<18} P/L {r['pnl']:>9.2f}  obchodov {r['trades']:>5}  "
              f"DD {r['max_dd']:>8.2f}  ratio {r['ratio']:>5.3f}  "
              f"open {r['open_end']}  funding {r['funding']:>7.2f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
