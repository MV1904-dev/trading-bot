#!/usr/bin/env python3
"""Lab: swingové stratégie na H4 s väčšími pozíciami (zadanie 16. 10. 2026).

Otázka: aká stratégia na 4h grafe s vyššími vstupmi (long 50 000,
short až 100 000) a dlhším držaním má najlepší pomer zisku k riziku?

Dáta: Dukascopy H1 BID 2012–2026 (mesačné súbory, cache data/lab_h4/),
H4 sa skladá z H1 (hranice 0/4/8/12/16/20 UTC).

Engine (spoločný pre všetky stratégie, jedna pozícia naraz):
* signál na uzavretej H4 sviečke, vstup na otvorení nasledujúcej H1
* SL/TP/trailing sa kontrolujú na H1 sviečkach — presnejšie než H4 OHLC;
  keď H1 zasiahne SL aj TP, počíta sa SL (konzervatívne)
* spread 0,1 pipu, provízia 0,526 € na 10 000 a cyklus, swap z
  trading/rates.py (rollover 21:00 UTC, streda 3×) — long platí, short
  dostáva, ako na live účte
* kapitál 15 200 €, marža 1:30; P/L v USD prepočítané na EUR

Hodnotenie je walk-forward: parametre sa vyberajú na 2012–2020
(in-sample) a výsledok sa hlási na 2021–2026 (out-of-sample), ktorý
výber nevidel. Stratégia, ktorá funguje len in-sample, je preladená.

    python3 strategy_lab_h4.py            # stiahne dáta (raz), spustí všetko
"""

from __future__ import annotations

import csv
import itertools
import lzma
import math
import struct
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from trading.rates import funding_rates_pa  # noqa: E402

DATA = ROOT / "data" / "lab_h4"
URL = "https://datafeed.dukascopy.com/datafeed/EURUSD/{y}/{m:02d}/BID_candles_hour_1.bi5"
UA = {"User-Agent": "Mozilla/5.0 (strategy-lab)"}

CAPITAL = 15_200.0
SPREAD = 0.00001
COMM_PER_10K = 0.526
LEVERAGE = 30.0
QTY_LONG = 50_000.0
QTY_SHORT = 100_000.0
IS_END = datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp()
LIVE_START = datetime(2026, 8, 6, tzinfo=timezone.utc).timestamp()


# --- dáta -------------------------------------------------------------------

def _month(y: int, m: int) -> list[tuple]:
    cache = DATA / f"{y}{m:02d}.bi5"
    now = datetime.now(timezone.utc)
    if cache.exists():
        raw = cache.read_bytes()
    else:
        url = URL.format(y=y, m=m - 1)
        raw = b""
        for attempt in range(8):
            try:
                raw = urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=60).read()
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
            time.sleep(min(30, 2 ** attempt))
        if (y, m) < (now.year, now.month):
            DATA.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(raw)
    if not raw:
        return []
    b = lzma.decompress(raw)
    base = datetime(y, m, 1, tzinfo=timezone.utc).timestamp()
    out = []
    for i in range(len(b) // 24):
        t, o, c, lo, hi, v = struct.unpack(">5if", b[i * 24:(i + 1) * 24])
        if v <= 0:
            continue
        out.append((int(base + t), o / 1e5, hi / 1e5, lo / 1e5, c / 1e5))
    return out


def load_h1(y0: int = 2012) -> list[tuple]:
    out_csv = DATA / "EURUSD_H1.csv"
    if out_csv.exists() and time.time() - out_csv.stat().st_mtime < 86400:
        with out_csv.open() as f:
            return [(int(r[0]), *map(float, r[1:])) for r in csv.reader(f)]
    now = datetime.now(timezone.utc)
    bars = []
    for y in range(y0, now.year + 1):
        for m in range(1, 13):
            if (y, m) > (now.year, now.month):
                break
            bars.extend(_month(y, m))
    # Mesiace, ktoré Dukascopy ako H1 súbor nevrátil (8/2026, bežiaci mesiac),
    # doplníme z M5 dát labu (strategy_lab_ladder.py), agregovaných na H1.
    m5 = ROOT / "data" / "lab_ladder" / "EURUSD_M5_20241015_20261015.csv"
    if m5.exists():
        have = {int(b[0] // 3600) for b in bars}
        agg: dict[int, list] = {}
        with m5.open() as f:
            for r in csv.reader(f):
                t, o, h, l, c = int(float(r[0])), *map(float, r[1:])
                k = t // 3600
                if k in have:
                    continue
                if k not in agg:
                    agg[k] = [k * 3600, o, h, l, c]
                else:
                    a = agg[k]
                    a[2], a[3], a[4] = max(a[2], h), min(a[3], l), c
        bars.extend(tuple(v) for v in agg.values())
    bars.sort()
    DATA.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as f:
        csv.writer(f).writerows(bars)
    return bars


def to_h4(h1: list[tuple]) -> tuple[list[tuple], list[int]]:
    """H4 sviečky + index poslednej H1 v každej H4 (na exekúciu)."""
    h4, last_idx = [], []
    for i, (t, o, h, l, c) in enumerate(h1):
        k = int(t // 14400)
        if h4 and h4[-1][0] == k * 14400:
            _, o0, h0, l0, _ = h4[-1]
            h4[-1] = (k * 14400, o0, max(h0, h), min(l0, l), c)
            last_idx[-1] = i
        else:
            h4.append((k * 14400, o, h, l, c))
            last_idx.append(i)
    return h4, last_idx


# --- indikátory ----------------------------------------------------------------

def ema(xs: list[float], n: int) -> list[float]:
    k, out, v = 2 / (n + 1), [], None
    for x in xs:
        v = x if v is None else v + k * (x - v)
        out.append(v)
    return out


def atr(bars: list[tuple], n: int = 14) -> list[float]:
    out, v, pc = [], None, None
    for _, o, h, l, c in bars:
        tr = h - l if pc is None else max(h - l, abs(h - pc), abs(l - pc))
        v = tr if v is None else v + (tr - v) / n
        out.append(v)
        pc = c
    return out


def rsi(closes: list[float], n: int) -> list[float]:
    out, ag, al = [], None, None
    for i, c in enumerate(closes):
        if i == 0:
            out.append(50.0)
            continue
        d = c - closes[i - 1]
        g, lo = max(d, 0.0), max(-d, 0.0)
        ag = g if ag is None else ag + (g - ag) / n
        al = lo if al is None else al + (lo - al) / n
        out.append(100.0 if al == 0 else 100 - 100 / (1 + ag / al))
    return out


def rolling(xs: list[float], n: int, fn) -> list[float | None]:
    return [None if i < n else fn(xs[i - n:i]) for i in range(len(xs))]


# --- engine ---------------------------------------------------------------------

@dataclass
class Trade:
    side: str
    qty: float
    entry: float
    t_in: float
    sl: float
    tp: float | None
    trail: float | None = None      # vzdialenosť trailing stopu v cene
    swap: float = 0.0
    best: float = 0.0


@dataclass
class Result:
    name: str
    trades: list = field(default_factory=list)   # (t_in, t_out, side, net €)
    curve: list = field(default_factory=list)    # (t, equity)

    def stats(self, t0: float = 0, t1: float = 9e12) -> dict:
        tr = [x for x in self.trades if t0 <= x[1] < t1]
        cur = [(t, e) for t, e in self.curve if t0 <= t < t1]
        if not cur:
            return {}
        base = cur[0][1]
        peak, dd = base, 0.0
        for _, e in cur:
            peak = max(peak, e)
            dd = max(dd, peak - e)
        net = cur[-1][1] - base
        wins = [x[3] for x in tr if x[3] > 0]
        loss = [x[3] for x in tr if x[3] <= 0]
        years = max((cur[-1][0] - cur[0][0]) / 31_557_600, 0.1)
        return {
            "obchodov": len(tr),
            "úspešnosť": f"{len(wins) / len(tr) * 100:.0f} %" if tr else "—",
            "PF": round(sum(wins) / -sum(loss), 2) if loss and sum(loss) else None,
            "výsledok": round(net),
            "€/rok": round(net / years),
            "max prepad": round(dd),
            "výsl/prepad": round(net / dd, 2) if dd else None,
            "priem. držanie": f"{sum(x[1] - x[0] for x in tr) / len(tr) / 86400:.1f} d" if tr else "—",
        }


def simulate(name: str, h1: list[tuple], h4: list[tuple], last_idx: list[int],
             signal, t_start: float = 0, t_end: float = 9e12) -> Result:
    """signal(i, pos) na uzavretej H4 sviečke i → None | ("long"|"short", sl,
    tp, trail) na vstup, alebo "close" na zatvorenie. Najviac jedna pozícia.

    Každé obdobie [t_start, t_end) je samostatný účet s čistým kapitálom —
    inak by straty z jedného obdobia vyčerpali účet pre ďalšie a tie by
    končili samými stop-outmi. Indikátory sú zahriate z celej histórie."""
    res = Result(name)
    eq = CAPITAL
    pos: Trade | None = None
    nxt = {li + 1: i for i, li in enumerate(last_idx)}     # H1 index → H4 signál
    pending = None
    j0 = next((k for k, b in enumerate(h1) if b[0] >= t_start), len(h1))
    last_roll = h1[j0][0] if j0 < len(h1) else 0

    def close(p: Trade, px: float, t: float) -> None:
        nonlocal eq
        diff = px - p.entry if p.side == "long" else p.entry - px
        gross = diff * p.qty / px
        net = gross - COMM_PER_10K * p.qty / 10_000 + p.swap
        eq += net
        res.trades.append((p.t_in, t, p.side, net))

    for j in range(j0, len(h1)):
        t, o, h, l, c = h1[j]
        if t >= t_end:
            break
        # vstup / výstup podľa signálu z predošlej H4
        if pending is not None:
            if pending == "close" and pos is not None:
                close(pos, o if pos.side == "long" else o + SPREAD, t)
                pos = None
            elif isinstance(pending, tuple) and pos is None:
                side, sl, tp, trail = pending
                qty = QTY_LONG if side == "long" else QTY_SHORT
                entry = o + SPREAD if side == "long" else o
                pos = Trade(side, qty, entry, t, sl, tp, trail, best=entry)
            pending = None
        # swap za rollovery
        d = datetime.fromtimestamp(last_roll, timezone.utc).replace(
            hour=21, minute=0, second=0, microsecond=0)
        if d.timestamp() <= last_roll:
            d += timedelta(days=1)
        while d.timestamp() <= t:
            if pos is not None and d.weekday() < 5:
                lp, sp = funding_rates_pa(d.strftime("%Y-%m-%d"))
                pa = -lp if pos.side == "long" else sp
                pos.swap += pa / 100 / 365 * pos.qty * (3 if d.weekday() == 2 else 1)
            d += timedelta(days=1)
        last_roll = t
        # SL / TP / trailing v H1 sviečke
        if pos is not None:
            if pos.side == "long":
                if l <= pos.sl:
                    close(pos, min(pos.sl, o), t)
                    pos = None
                elif pos.tp is not None and h >= pos.tp:
                    close(pos, pos.tp, t)
                    pos = None
                elif pos.trail:
                    pos.best = max(pos.best, h)
                    pos.sl = max(pos.sl, pos.best - pos.trail)
            else:
                if h + SPREAD >= pos.sl:
                    close(pos, max(pos.sl, o + SPREAD), t)
                    pos = None
                elif pos.tp is not None and l + SPREAD <= pos.tp:
                    close(pos, pos.tp, t)
                    pos = None
                elif pos.trail:
                    pos.best = min(pos.best, l)
                    pos.sl = min(pos.sl, pos.best + pos.trail)
        # equity + stop-out (50 %)
        fl = 0.0
        if pos is not None:
            px = c if pos.side == "long" else c + SPREAD
            fl = ((px - pos.entry) if pos.side == "long" else (pos.entry - px)) * pos.qty / px + pos.swap
            used = pos.qty / LEVERAGE
            if (eq + fl) / used < 0.5:
                close(pos, px, t)
                pos, fl = None, 0.0
        res.curve.append((t, eq + fl))
        # signál po uzavretí H4
        if j + 1 in nxt:
            pending = signal(nxt[j + 1], pos)
    return res


# --- stratégie -------------------------------------------------------------------

def strat_donchian(h4, n_in=20, n_out=10, atr_sl=2.0, side_ok=("long", "short")):
    """Prieraz Donchianovho kanála (Turtle): vstup na n_in maxime/minime,
    výstup na n_out opačnom extréme, počiatočný SL atr_sl × ATR."""
    hi = rolling([b[2] for b in h4], n_in, max)
    lo = rolling([b[3] for b in h4], n_in, min)
    xhi = rolling([b[2] for b in h4], n_out, max)
    xlo = rolling([b[3] for b in h4], n_out, min)
    a = atr(h4)

    def sig(i, pos):
        c = h4[i][4]
        if hi[i] is None:
            return None
        if pos is not None:
            if pos.side == "long" and c < xlo[i]:
                return "close"
            if pos.side == "short" and c > xhi[i]:
                return "close"
            return None
        if c > hi[i] and "long" in side_ok:
            return ("long", c - atr_sl * a[i], None, None)
        if c < lo[i] and "short" in side_ok:
            return ("short", c + atr_sl * a[i], None, None)
        return None
    return sig


def strat_ema_trend(h4, fast=20, slow=100, atr_sl=2.5, trail=3.0, side_ok=("long", "short")):
    """Trend: vstup pri prekrížení EMA, ATR trailing stop."""
    cl = [b[4] for b in h4]
    ef, es, a = ema(cl, fast), ema(cl, slow), atr(h4)

    def sig(i, pos):
        if i < slow:
            return None
        up, was = ef[i] > es[i], ef[i - 1] > es[i - 1]
        c = cl[i]
        if pos is not None:
            if (pos.side == "long" and not up) or (pos.side == "short" and up):
                return "close"
            return None
        if up and not was and "long" in side_ok:
            return ("long", c - atr_sl * a[i], None, trail * a[i])
        if not up and was and "short" in side_ok:
            return ("short", c + atr_sl * a[i], None, trail * a[i])
        return None
    return sig


def strat_pullback(h4, trend=200, rsi_n=2, lo_th=10, hi_th=None, atr_sl=2.0, tp_atr=2.0,
                   max_bars=30, side_ok=("long", "short")):
    """Návrat v trende: nad EMA(trend) kúp prepredaný RSI(2), pod ňou
    predaj prekúpený. SL a TP v násobkoch ATR, časový stop."""
    cl = [b[4] for b in h4]
    et, r, a = ema(cl, trend), rsi(cl, rsi_n), atr(h4)
    hi_th = 100 - lo_th if hi_th is None else hi_th

    def sig(i, pos):
        if i < trend:
            return None
        c = cl[i]
        if pos is not None:
            if (h4[i][0] + 14400 - pos.t_in) / 14400 >= max_bars:
                return "close"
            return None
        if c > et[i] and r[i] < lo_th and "long" in side_ok:
            return ("long", c - atr_sl * a[i], c + tp_atr * a[i], None)
        if c < et[i] and r[i] > hi_th and "short" in side_ok:
            return ("short", c + atr_sl * a[i], c - tp_atr * a[i], None)
        return None
    return sig


def strat_bollinger_mr(h4, n=20, k=2.0, atr_sl=2.0, side_ok=("long", "short")):
    """Návrat k priemeru: zatvorenie za Bollingerovým pásmom → vstup proti,
    výstup na strednej čiare, SL v ATR."""
    cl = [b[4] for b in h4]
    a = atr(h4)
    mean = rolling(cl, n, lambda w: sum(w) / len(w))
    sd = rolling(cl, n, lambda w: math.sqrt(sum((x - sum(w) / len(w)) ** 2 for x in w) / len(w)))

    def sig(i, pos):
        if mean[i] is None:
            return None
        c, m, s = cl[i], mean[i], sd[i]
        if pos is not None:
            if (pos.side == "long" and c >= m) or (pos.side == "short" and c <= m):
                return "close"
            return None
        if c < m - k * s and "long" in side_ok:
            return ("long", c - atr_sl * a[i], None, None)
        if c > m + k * s and "short" in side_ok:
            return ("short", c + atr_sl * a[i], None, None)
        return None
    return sig


def strat_momentum(h4, look=120, atr_sl=3.0, trail=4.0, side_ok=("long", "short")):
    """Časová hybnosť: smer podľa výnosu za `look` H4 sviečok (≈ 20 dní),
    otočenie pri zmene znamienka, ATR trailing."""
    cl = [b[4] for b in h4]
    a = atr(h4)

    def sig(i, pos):
        if i < look:
            return None
        up = cl[i] > cl[i - look]
        c = cl[i]
        if pos is not None:
            if (pos.side == "long" and not up) or (pos.side == "short" and up):
                return "close"
            return None
        if up and "long" in side_ok:
            return ("long", c - atr_sl * a[i], None, trail * a[i])
        if not up and "short" in side_ok:
            return ("short", c + atr_sl * a[i], None, trail * a[i])
        return None
    return sig


SIDES = [("long", "short"), ("short",), ("long",)]
FAMILIES = {
    "Donchian prieraz": (strat_donchian, {
        "n_in": [20, 55, 110], "n_out": [10, 20, 55], "atr_sl": [2.0, 3.0], "side_ok": SIDES}),
    "EMA trend + trailing": (strat_ema_trend, {
        "fast": [10, 20, 50], "slow": [50, 100, 200], "atr_sl": [2.0, 3.0],
        "trail": [2.0, 3.0, 4.0], "side_ok": SIDES}),
    "Pullback v trende (RSI2)": (strat_pullback, {
        "trend": [100, 200], "lo_th": [5, 10, 20], "atr_sl": [1.5, 2.5],
        "tp_atr": [1.0, 2.0, 3.0], "side_ok": SIDES}),
    "Bollinger návrat": (strat_bollinger_mr, {
        "n": [20, 50], "k": [2.0, 2.5], "atr_sl": [1.5, 2.5, 4.0], "side_ok": SIDES}),
    "Hybnosť 20 dní": (strat_momentum, {
        "look": [60, 120, 240], "atr_sl": [2.0, 3.0], "trail": [3.0, 4.0, 6.0], "side_ok": SIDES}),
}


def main() -> int:
    t0 = time.time()
    h1 = load_h1()
    h4, last_idx = to_h4(h1)
    print(f"dáta: {len(h1)} H1 / {len(h4)} H4 sviečok, "
          f"{datetime.fromtimestamp(h1[0][0], timezone.utc):%d. %m. %Y} – "
          f"{datetime.fromtimestamp(h1[-1][0], timezone.utc):%d. %m. %Y} "
          f"({time.time() - t0:.0f} s)")
    rows = []
    robust = []
    for fam, (fn, grid) in FAMILIES.items():
        keys = list(grid)
        best = None
        oos_all = []
        for combo in itertools.product(*(grid[k] for k in keys)):
            kw = dict(zip(keys, combo))
            if "n_out" in kw and kw["n_out"] > kw["n_in"]:
                continue
            if "fast" in kw and kw["fast"] >= kw["slow"]:
                continue
            r = simulate(fam, h1, h4, last_idx, fn(h4, **kw), 0, IS_END)
            ins = r.stats()
            # aspoň 30 obchodov in-sample, inak je „najlepší" výsledok náhoda
            score = (ins.get("výsl/prepad") or -9) if ins.get("obchodov", 0) >= 30 else -9
            if best is None or score > best[0]:
                best = (score, kw, r)
            o = simulate(fam, h1, h4, last_idx, fn(h4, **kw), IS_END, 9e12).stats()
            oos_all.append(o.get("výsl/prepad") or 0)
        _, kw, r_is = best
        oos_all.sort()
        robust.append((fam, len(oos_all), sum(x > 0 for x in oos_all),
                       oos_all[len(oos_all) // 2], oos_all[-1]))
        for label, a, b in (("in-sample 2012–20", 0, IS_END),
                            ("OOS 2021–26", IS_END, 9e12),
                            ("live obdobie", LIVE_START, 9e12)):
            r = r_is if a == 0 else simulate(fam, h1, h4, last_idx, FAMILIES[fam][0](h4, **kw), a, b)
            st = r.stats()
            rows.append({"rodina": fam, "obdobie": label, **st,
                         "parametre": " ".join(f"{k}={'/'.join(v) if isinstance(v, tuple) else v}"
                                               for k, v in kw.items())
                         if label.startswith("in") else ""})
        print(f"  {fam}: hotovo ({time.time() - t0:.0f} s)")
    keys = list(rows[0])
    w = {k: max(len(k), *(len(str(r.get(k, ''))) for r in rows)) for k in keys}
    print()
    print("  ".join(k.ljust(w[k]) for k in keys))
    for r in rows:
        print("  ".join(str(r.get(k, "")).ljust(w[k]) for k in keys))
    print("\nRobustnosť — všetky nastavenia rodiny na OOS 2021–26 (výsledok / prepad):")
    for fam, n, pos, med, top in robust:
        print(f"  {fam:26} {pos:3}/{n} nastavení v zisku, medián {med:+.2f}, najlepšie {top:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
