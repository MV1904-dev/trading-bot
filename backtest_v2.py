"""Backtest v2 — pásmová grid stratégia EURUSD nad IBKR dátami.

Použitie
--------
    python3 backtest_v2.py               # všetko: IBKR M5 + stresové obdobia
    python3 backtest_v2.py --no-stress   # len IBKR M5 (bez sťahovania Dukascopy)

Dáta
----
* Primárne: ``data/ibkr_EURUSD_M5.csv`` (cache z trading/broker_ibkr.py).
* Stres: Dukascopy verejný datafeed, EURUSD H1 2013–2023 (bi5/LZMA, čisté
  stdlib). Cache v ``data/dukascopy_EURUSD_H1.csv``. Stratégia beží zvlášť na
  2014–2015 (pád 1.39 → 1.05) a 2021–2022 (pád 1.23 → 0.95).

Stratégia (pásmový grid)
------------------------
* Pod 1.1200 len long grid; nad 1.1600 len short grid; medzi tým obojsmerne.
* Short vstup pri raste o `step` od lokálneho minima / poslednej úrovne.
* Long vstup pri poklese o `1.5 × step` od lokálneho maxima / poslednej
  úrovne — pomer 1.5 zachováva zadané 0.10 % / 0.15 %. Long navyše vyžaduje
  pokles > 2× ATR(14) (t. j. trigger = max(krok, 2× ATR)).
* Každá pozícia má vlastný TP +0.1 % vo svoj prospech, žiadny SL.
* Kapacita 20 úrovní na smer + 10 rezervných na smer; rezervné sa odomknú,
  len ak je cena > 2× ATR od poslednej úrovne daného smeru.
* Vstupy sa vyhodnocujú na close baru (max 1 vstup na smer a bar), TP na
  high/low baru od nasledujúceho baru.

Náklady (IBKR IDEALPRO)
-----------------------
* Provízia 0.2 bps z USD hodnoty, min 2 USD na príkaz (vstup aj výstup).
* Spread: dáta sú MIDPOINT, preto paušál 0.1 pipu na round-trip
  (0.05 pipu na fill).
* Funding cez noc z rozdielu sadzieb Fed − ECB (tabuľka nižšie, mesačná
  granularita, približné oficiálne sadzby): long platí (rozdiel + 1 %) p.a.,
  short inkasuje max(rozdiel − 1 %, 0) p.a. Účtuje sa za každý kalendárny
  deň držania (aproximuje víkendové trojité swapy).

Výstupy
-------
``data/backtest_v2/results.csv`` + tabuľka na stdout + equity PNG pre top 3
varianty každého datasetu. Meny: P/L a náklady v EUR (prepočet z USD kurzom
v čase transakcie). maxDD % je vztiahnuté na min. kapitál pri páke 1:30.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import io
import lzma
import os
import struct
import sys
import time as _time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUT_DIR = DATA_DIR / "backtest_v2"

IBKR_CSV = DATA_DIR / "ibkr_EURUSD_M5.csv"
DUKA_CSV = DATA_DIR / "dukascopy_EURUSD_H1.csv"

# --- Parametre stratégie ----------------------------------------------------
BAND_LOW = 1.1200          # pod: len long grid
BAND_HIGH = 1.1600         # nad: len short grid
TP_PCT = 0.001             # TP +0.1 % vo svoj prospech
LONG_STEP_RATIO = 1.5      # long krok = 1.5 × short krok (0.10 % vs 0.15 %)
ATR_PERIOD = 14
ATR_MULT = 2.0             # long filter aj odomknutie rezervných úrovní
BASE_LEVELS = 20           # bežné úrovne na smer
RESERVE_LEVELS = 10        # rezervné úrovne na smer
TREND_DAY_PCT = 0.005      # deň s |close-open| > 0.5 % považujeme za trendový

# --- Náklady ----------------------------------------------------------------
COMMISSION_BPS = 0.2e-4    # 0.2 bps z USD hodnoty
COMMISSION_MIN = 2.0       # min 2 USD / príkaz
HALF_SPREAD = 0.000005     # 0.05 pipu na fill (0.1 pipu round-trip)
FUNDING_MARKUP = 1.0       # broker prirážka v % p.a.

# --- Približné sadzby (mesačná granularita stačí na funding odhad) ----------
# (dátum účinnosti, sadzba %) — Fed horná hranica pásma, ECB depozitná.
FED_RATES = [
    ("2013-01-01", 0.25), ("2015-12-17", 0.50), ("2016-12-15", 0.75),
    ("2017-03-16", 1.00), ("2017-06-15", 1.25), ("2017-12-14", 1.50),
    ("2018-03-22", 1.75), ("2018-06-14", 2.00), ("2018-09-27", 2.25),
    ("2018-12-20", 2.50), ("2019-08-01", 2.25), ("2019-09-19", 2.00),
    ("2019-10-31", 1.75), ("2020-03-04", 1.25), ("2020-03-16", 0.25),
    ("2022-03-17", 0.50), ("2022-05-05", 1.00), ("2022-06-16", 1.75),
    ("2022-07-28", 2.50), ("2022-09-22", 3.25), ("2022-11-03", 4.00),
    ("2022-12-15", 4.50), ("2023-02-02", 4.75), ("2023-03-23", 5.00),
    ("2023-05-04", 5.25), ("2023-07-27", 5.50), ("2024-09-19", 5.00),
    ("2024-11-08", 4.75), ("2024-12-19", 4.50), ("2025-09-18", 4.25),
    ("2025-10-30", 4.00), ("2025-12-11", 3.75),
]
ECB_RATES = [
    ("2013-01-01", 0.00), ("2014-06-11", -0.10), ("2014-09-10", -0.20),
    ("2015-12-09", -0.30), ("2016-03-16", -0.40), ("2019-09-18", -0.50),
    ("2022-07-27", 0.00), ("2022-09-14", 0.75), ("2022-11-02", 1.50),
    ("2022-12-21", 2.00), ("2023-02-08", 2.50), ("2023-03-22", 3.00),
    ("2023-05-10", 3.25), ("2023-06-21", 3.50), ("2023-08-02", 3.75),
    ("2023-09-20", 4.00), ("2024-06-12", 3.75), ("2024-09-18", 3.50),
    ("2024-10-23", 3.25), ("2024-12-18", 3.00), ("2025-02-05", 2.75),
    ("2025-03-12", 2.50), ("2025-04-23", 2.25), ("2025-06-11", 2.00),
]

_FED_DATES = [d for d, _ in FED_RATES]
_ECB_DATES = [d for d, _ in ECB_RATES]


def rate_diff(day: str) -> float:
    """Fed − ECB v % p.a. k danému dňu ('YYYY-MM-DD')."""
    fed = FED_RATES[max(bisect.bisect_right(_FED_DATES, day) - 1, 0)][1]
    ecb = ECB_RATES[max(bisect.bisect_right(_ECB_DATES, day) - 1, 0)][1]
    return fed - ecb


# --- Dáta -------------------------------------------------------------------

@dataclass
class Bars:
    name: str
    t: np.ndarray        # epoch sekundy (UTC)
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    def __len__(self):
        return len(self.t)

    def describe(self) -> str:
        d0 = datetime.fromtimestamp(int(self.t[0]), tz=timezone.utc)
        d1 = datetime.fromtimestamp(int(self.t[-1]), tz=timezone.utc)
        return (f"{self.name}: {len(self):,} barov, {d0:%Y-%m-%d} → {d1:%Y-%m-%d}, "
                f"close {self.close[0]:.4f} → {self.close[-1]:.4f}")


def load_ibkr_csv(path: Path) -> Bars:
    """Loader pre IBKR cache formát (date,open,high,low,close,...)."""
    ts, o, h, l, c = [], [], [], [], []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            dt = datetime.fromisoformat(row["date"])
            ts.append(dt.timestamp())
            o.append(float(row["open"]))
            h.append(float(row["high"]))
            l.append(float(row["low"]))
            c.append(float(row["close"]))
    order = np.argsort(np.asarray(ts))
    return Bars("IBKR_M5", np.asarray(ts)[order], np.asarray(o)[order],
                np.asarray(h)[order], np.asarray(l)[order], np.asarray(c)[order])


def _duka_month(year: int, month0: int) -> bytes:
    url = (f"https://datafeed.dukascopy.com/datafeed/EURUSD/"
           f"{year}/{month0:02d}/BID_candles_hour_1.bi5")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _parse_bi5(raw_c: bytes, month_epoch: float) -> list[tuple]:
    """Vráti [(epoch, o, h, l, c), ...]; formát Dukascopy je
    [offset_s, open, close, low, high, vol] × 1e5, auto-overí sa poradie."""
    if not raw_c:
        return []
    raw = lzma.decompress(raw_c)
    n = len(raw) // 24
    recs = [struct.unpack_from(">IIIIIf", raw, i * 24) for i in range(n)]

    def violations(order):  # order = indexy (o, h, l, c) v zázname 1..4
        bad = 0
        for r in recs:
            o, h, l, c = (r[order[0]], r[order[1]], r[order[2]], r[order[3]])
            if h < max(o, c) or l > min(o, c):
                bad += 1
        return bad

    oclh = (1, 4, 3, 2)      # open, close, low, high (dokumentovaný formát)
    ohlc = (1, 2, 3, 4)
    order = oclh if violations(oclh) <= violations(ohlc) else ohlc

    out = []
    for r in recs:
        o, h, l, c = (r[order[0]] / 1e5, r[order[1]] / 1e5,
                      r[order[2]] / 1e5, r[order[3]] / 1e5)
        if r[5] == 0.0 and o == h == l == c:
            continue                       # víkend/sviatok — placeholder
        out.append((month_epoch + r[0], o, h, l, c))
    return out


def load_dukascopy_h1(y0: int = 2013, y1: int = 2023) -> Bars:
    """Stiahne (a nacachuje) EURUSD H1 z Dukascopy za roky y0..y1."""
    if DUKA_CSV.exists():
        ts, o, h, l, c = [], [], [], [], []
        with open(DUKA_CSV, newline="") as f:
            for row in csv.DictReader(f):
                ts.append(float(row["epoch"]))
                o.append(float(row["open"])); h.append(float(row["high"]))
                l.append(float(row["low"])); c.append(float(row["close"]))
        return Bars("DUKA_H1", np.asarray(ts), np.asarray(o), np.asarray(h),
                    np.asarray(l), np.asarray(c))

    print(f"Sťahujem Dukascopy EURUSD H1 {y0}–{y1} "
          f"({(y1 - y0 + 1) * 12} mesačných súborov)…")
    rows: list[tuple] = []
    for year in range(y0, y1 + 1):
        for m0 in range(12):
            epoch = datetime(year, m0 + 1, 1, tzinfo=timezone.utc).timestamp()
            try:
                rows += _parse_bi5(_duka_month(year, m0), epoch)
            except Exception as exc:  # noqa: BLE001 — chýbajúci mesiac preskoč
                print(f"  ({year}-{m0 + 1:02d}: {exc})")
            _time.sleep(0.15)
        print(f"  {year}: spolu {len(rows):,} barov")
    rows.sort(key=lambda r: r[0])

    DATA_DIR.mkdir(exist_ok=True)
    with open(DUKA_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch", "open", "high", "low", "close"])
        w.writerows(rows)
    arr = np.asarray(rows)
    return Bars("DUKA_H1", arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4])


def slice_years(bars: Bars, name: str, yfrom: int, yto: int) -> Bars:
    t0 = datetime(yfrom, 1, 1, tzinfo=timezone.utc).timestamp()
    t1 = datetime(yto + 1, 1, 1, tzinfo=timezone.utc).timestamp()
    m = (bars.t >= t0) & (bars.t < t1)
    return Bars(name, bars.t[m], bars.open[m], bars.high[m],
                bars.low[m], bars.close[m])


def atr_wilder(bars: Bars, period: int = ATR_PERIOD) -> np.ndarray:
    h, l, c = bars.high, bars.low, bars.close
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = np.empty_like(tr)
    atr[:period] = np.nan
    atr[period - 1] = tr[:period].mean()
    k = 1.0 / period
    for i in range(period, len(tr)):
        atr[i] = atr[i - 1] * (1 - k) + tr[i] * k
    return atr


# --- Simulátor --------------------------------------------------------------

@dataclass
class Variant:
    size: float            # EUR notional na jednu grid pozíciu
    step: float            # short krok (frakcia, napr. 0.001 = 0.10 %)
    bands: bool

    @property
    def key(self) -> str:
        return (f"{int(self.size / 1000)}k_"
                f"{self.step * 1e4:.0f}bp_{'pasma' if self.bands else 'bez'}")


@dataclass
class Result:
    dataset: str
    variant: Variant
    days: int = 0
    trend_days: int = 0
    pnl_total: float = 0.0        # EUR, vrátane floatingu na konci a nákladov
    pnl_realized: float = 0.0     # EUR, čisté realizované (po nákladoch)
    floating_end: float = 0.0
    cycles: int = 0
    opened: int = 0
    open_end: int = 0
    cyc_day_trend: float = 0.0
    cyc_day_side: float = 0.0
    max_dd: float = 0.0           # EUR, drawdown equity (vrátane floatingu)
    max_expo: float = 0.0         # EUR notional
    min_cap: float = 0.0          # min. kapitál pri páke 1:30
    commissions: float = 0.0      # EUR
    spread_cost: float = 0.0      # EUR
    funding: float = 0.0          # EUR, záporné = čistý náklad
    equity_t: list = field(default_factory=list)
    equity_v: list = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return 100.0 * self.cycles / self.opened if self.opened else 0.0

    @property
    def cyc_per_day(self) -> float:
        return self.cycles / self.days if self.days else 0.0

    @property
    def dd_pct(self) -> float:
        return 100.0 * self.max_dd / self.min_cap if self.min_cap > 0 else 0.0


def run_variant(bars: Bars, atr: np.ndarray, v: Variant) -> Result:
    res = Result(bars.name, v)
    size = v.size
    step_s, step_l = v.step, v.step * LONG_STEP_RATIO
    cap = BASE_LEVELS + RESERVE_LEVELS

    longs: list[float] = []       # entry ceny
    shorts: list[float] = []
    last_long = last_short = 0.0  # posledná úroveň (pre rezervné pravidlo)
    ref_long = ref_short = bars.close[0]

    realized = comm = spread = funding = 0.0   # EUR
    peak = dd = min_cap = max_expo = 0.0
    cycles_by_day: dict[str, int] = {}
    day_oc: dict[str, list] = {}               # deň -> [open, close]

    sample_every = max(1, len(bars) // 4000)
    prev_day = datetime.fromtimestamp(int(bars.t[0]), tz=timezone.utc).date()

    def fill_costs(price: float) -> float:
        """EUR náklady jedného fillu (provízia + polovica spreadu)."""
        c_usd = max(COMMISSION_MIN, size * price * COMMISSION_BPS)
        s_usd = size * HALF_SPREAD
        nonlocal comm, spread
        comm += c_usd / price
        spread += s_usd / price
        return (c_usd + s_usd) / price

    for i in range(len(bars)):
        t, o, h, l, c = bars.t[i], bars.open[i], bars.high[i], bars.low[i], bars.close[i]
        day = datetime.fromtimestamp(int(t), tz=timezone.utc).date()
        dkey = day.isoformat()
        rec = day_oc.setdefault(dkey, [o, c])
        rec[1] = c

        # 1) funding pri zmene kalendárneho dňa (za každý preklenutý deň)
        if day != prev_day:
            ndays = (day - prev_day).days
            diff = rate_diff(dkey)
            long_pa = max(diff + FUNDING_MARKUP, 0.0) / 100.0
            short_pa = max(diff - FUNDING_MARKUP, 0.0) / 100.0
            f_usd = (-long_pa * sum(size * c for _ in longs)
                     + short_pa * sum(size * c for _ in shorts)) * ndays / 365.0
            funding += f_usd / c
            prev_day = day

        # 2) TP výstupy na high/low
        if longs:
            still = []
            for e in longs:
                tp = e * (1 + TP_PCT)
                if h >= tp:
                    realized += (tp - e) * size / tp - fill_costs(tp)
                    res.cycles += 1
                    cycles_by_day[dkey] = cycles_by_day.get(dkey, 0) + 1
                else:
                    still.append(e)
            longs = still
            if not longs:
                ref_long = c
        if shorts:
            still = []
            for e in shorts:
                tp = e * (1 - TP_PCT)
                if l <= tp:
                    realized += (e - tp) * size / tp - fill_costs(tp)
                    res.cycles += 1
                    cycles_by_day[dkey] = cycles_by_day.get(dkey, 0) + 1
                else:
                    still.append(e)
            shorts = still
            if not shorts:
                ref_short = c

        # 3) referenčné extrémy
        ref_long = max(ref_long, h)
        ref_short = min(ref_short, l)

        # 4) vstupy na close (max 1 / smer / bar)
        a = atr[i]
        if not np.isnan(a):
            allow_long = (not v.bands) or (c < BAND_HIGH)
            allow_short = (not v.bands) or (c > BAND_LOW)

            if allow_long and len(longs) < cap:
                drop = ref_long - c
                trigger = max(ref_long * step_l, ATR_MULT * a)  # ATR filter longov
                unlock = (len(longs) < BASE_LEVELS
                          or abs(c - last_long) > ATR_MULT * a)
                if drop >= trigger and unlock:
                    realized -= fill_costs(c)
                    longs.append(c)
                    res.opened += 1
                    last_long = c
                    ref_long = c

            if allow_short and len(shorts) < cap:
                rise = c - ref_short
                unlock = (len(shorts) < BASE_LEVELS
                          or abs(c - last_short) > ATR_MULT * a)
                if rise >= ref_short * step_s and unlock:
                    realized -= fill_costs(c)
                    shorts.append(c)
                    res.opened += 1
                    last_short = c
                    ref_short = c

        # 5) equity, drawdown, kapitál
        float_usd = (sum(c - e for e in longs) + sum(e - c for e in shorts)) * size
        equity = realized + funding + float_usd / c
        expo = (len(longs) + len(shorts)) * size
        max_expo = max(max_expo, expo)
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
        min_cap = max(min_cap, expo / 30.0 - equity)
        if i % sample_every == 0 or i == len(bars) - 1:
            res.equity_t.append(t)
            res.equity_v.append(equity)

    # --- štatistiky dní ----------------------------------------------------
    trend_days = {d for d, (do, dc) in day_oc.items()
                  if abs(dc - do) / do > TREND_DAY_PCT}
    side_days = set(day_oc) - trend_days
    cyc_t = sum(cycles_by_day.get(d, 0) for d in trend_days)
    cyc_s = sum(cycles_by_day.get(d, 0) for d in side_days)

    c_end = bars.close[-1]
    float_end = (sum(c_end - e for e in longs) + sum(e - c_end for e in shorts)) * size / c_end

    res.days = len(day_oc)
    res.trend_days = len(trend_days)
    res.cyc_day_trend = cyc_t / len(trend_days) if trend_days else 0.0
    res.cyc_day_side = cyc_s / len(side_days) if side_days else 0.0
    res.pnl_realized = realized + funding
    res.floating_end = float_end
    res.pnl_total = realized + funding + float_end
    res.open_end = len(longs) + len(shorts)
    res.max_dd = dd
    res.max_expo = max_expo
    res.min_cap = max(min_cap, 0.0)
    res.commissions = comm
    res.spread_cost = spread
    res.funding = funding
    return res


# --- Výstup -----------------------------------------------------------------

CSV_COLS = [
    "dataset", "variant", "size_eur", "step_bp", "bands", "days", "trend_days",
    "pnl_total_eur", "pnl_realized_eur", "floating_end_eur",
    "cycles", "cycles_per_day", "cyc_day_trend", "cyc_day_side",
    "opened", "win_rate_pct", "open_end",
    "max_dd_eur", "max_dd_pct_of_min_cap", "max_exposure_eur",
    "min_capital_1to30_eur", "commissions_eur", "spread_eur", "funding_eur",
]


def result_row(r: Result) -> list:
    v = r.variant
    return [r.dataset, v.key, int(v.size), round(v.step * 1e4, 1), int(v.bands),
            r.days, r.trend_days,
            round(r.pnl_total, 2), round(r.pnl_realized, 2), round(r.floating_end, 2),
            r.cycles, round(r.cyc_per_day, 2), round(r.cyc_day_trend, 2),
            round(r.cyc_day_side, 2), r.opened, round(r.win_rate, 1), r.open_end,
            round(r.max_dd, 2), round(r.dd_pct, 1), int(r.max_expo),
            round(r.min_cap, 2), round(r.commissions, 2), round(r.spread_cost, 2),
            round(r.funding, 2)]


def print_table(results: list[Result]) -> None:
    hdr = (f"{'variant':<14}{'P/L €':>10}{'cykly':>7}{'c/d T':>7}{'c/d B':>7}"
           f"{'win%':>7}{'open':>6}{'maxDD €':>10}{'DD%':>6}{'expo €':>10}"
           f"{'kap 1:30':>10}{'prov €':>9}{'spr €':>9}{'fund €':>9}")
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(results, key=lambda x: -x.pnl_total):
        print(f"{r.variant.key:<14}{r.pnl_total:>10.0f}{r.cycles:>7}"
              f"{r.cyc_day_trend:>7.2f}{r.cyc_day_side:>7.2f}{r.win_rate:>7.1f}"
              f"{r.open_end:>6}{r.max_dd:>10.0f}{r.dd_pct:>6.0f}"
              f"{r.max_expo:>10.0f}{r.min_cap:>10.0f}{r.commissions:>9.0f}"
              f"{r.spread_cost:>9.2f}{r.funding:>9.0f}")


def save_equity_png(r: Result, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ts = [datetime.fromtimestamp(int(t), tz=timezone.utc) for t in r.equity_t]
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(ts, r.equity_v, lw=0.9)
    ax.axhline(0, color="grey", lw=0.6)
    ax.set_title(f"{r.dataset} — {r.variant.key}  "
                 f"(P/L {r.pnl_total:,.0f} €, maxDD {r.max_dd:,.0f} €)")
    ax.set_ylabel("equity € (realizované + floating)")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest v2 — grid EURUSD")
    ap.add_argument("--no-stress", action="store_true",
                    help="preskoč Dukascopy stresové obdobia")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    datasets: list[Bars] = []
    if not IBKR_CSV.exists():
        print(f"CHYBA: chýba {IBKR_CSV} (spusti trading/smoke_test).")
        return 1
    ib = load_ibkr_csv(IBKR_CSV)
    ib.name = "IBKR_M5"
    datasets.append(ib)

    if not args.no_stress:
        duka = load_dukascopy_h1()
        datasets.append(slice_years(duka, "STRES_2014-2015_H1", 2014, 2015))
        datasets.append(slice_years(duka, "STRES_2021-2022_H1", 2021, 2022))

    variants = [Variant(size=s, step=st, bands=b)
                for s in (10_000, 20_000, 25_000)
                for st in (0.0010, 0.0015, 0.0020)
                for b in (True, False)]

    all_results: list[Result] = []
    for bars in datasets:
        print(f"\n=== {bars.describe()} ===")
        atr = atr_wilder(bars)
        results = []
        for v in variants:
            r = run_variant(bars, atr, v)
            results.append(r)
            print(f"  {v.key:<14} hotovo: P/L {r.pnl_total:>10.0f} €, "
                  f"cykly {r.cycles}")
        print()
        print_table(results)
        for rank, r in enumerate(sorted(results, key=lambda x: -x.pnl_total)[:3], 1):
            png = OUT_DIR / f"equity_{r.dataset}_{rank}_{r.variant.key}.png"
            save_equity_png(r, png)
            print(f"  equity PNG: {png.relative_to(ROOT)}")
        all_results += results

    csv_path = OUT_DIR / "results.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLS)
        w.writerows(result_row(r) for r in all_results)
    print(f"\nVýsledky uložené do {csv_path.relative_to(ROOT)} "
          f"({len(all_results)} riadkov).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
