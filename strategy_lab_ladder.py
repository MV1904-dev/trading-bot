#!/usr/bin/env python3
"""Lab: pevná mriežka (ladder) proti dnešnému Grid25-G2B-CT.

Otázka zo 15. 10. 2026: bot nemá rozhodovať o cene, má byť dozorca. Vstupy
a výstupy sú pevné úrovne s krokom 0,1 % (= 10 EUR na cyklus pri 10 000 EUR),
čakajúce príkazy ležia u brokera a bot po každom TP len znovu založí vstup
na tej istej úrovni. Krok sa poruší iba pri gape — vtedy limitka padne na
gapovej cene a TP ostane na pôvodnej úrovni.

Porovnávané stratégie
---------------------
* G2B-CT   — dnešný bot (trading/strategy_grid25.py): kotva je bežiace
             maximum/minimum, po vstupe sa presunie na cenu plnenia, long
             navyše max(krok, 2×ATR), rezervné úrovne nad 20 len pri odstupe
             > 2×ATR. Rozhoduje na uzavretej M5 sviečke, vstup trhovým
             príkazom. Kroky podľa toho, ako reálne bežali (viď STEPS_LIVE).
* LADDER   — pevná geometrická mriežka L_k = (1 + krok)^k. Long: nákupná
             limitka na každej úrovni pod kurzom, TP o úroveň vyššie. Short
             zrkadlovo. Pásmo 1,12–1,16 a strop 30 na stranu ako dnes.

Spoločné pre obe
----------------
* dáta: Dukascopy M1 BID → M5 (obchodná cena long = bid + spread)
* spread 0,1 pipu, provízia 0,526 EUR na cyklus — priemer z live obchodov
* swap z trading/rates.py (Fed − ECB ± 1 %), rollover 21:00 UTC po–pi,
  v stredu 3× — model sedí s brokerom na ±5 % (−0,68 vs −0,65 EUR/noc long)
* marža 333 EUR na pozíciu (10 000 EUR pri páke 1:30)
* obe začínajú bez pozícií — staré pozície z live sa neprenášajú

Konzervatívne zjednodušenia (rovnaké pre obe): pozícia otvorená v sviečke
sa v tej istej sviečke nezavrie a úroveň zavretá na TP sa v tej istej
sviečke znovu neotvorí — poradie cien vnútri sviečky nepoznáme.

Použitie
--------
    python3 strategy_lab_ladder.py              # live obdobie + 2 roky
    python3 strategy_lab_ladder.py --live-only  # len od 6. 8. 2026
"""

from __future__ import annotations

import argparse
import csv
import lzma
import math
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from trading.rates import funding_rates_pa  # noqa: E402

DATA = ROOT / "data" / "lab_ladder"
DUKA = ("https://datafeed.dukascopy.com/datafeed/EURUSD/"
        "{y}/{m:02d}/{d:02d}/BID_candles_min_1.bi5")
UA = {"User-Agent": "Mozilla/5.0 (strategy-lab)"}

QTY = 10_000.0
SPREAD = 0.00001            # 0,1 pipu (live priemer 0,093)
COMMISSION = 0.526          # EUR na cyklus (live priemer)
MARGIN_PER_POS = QTY / 30   # 1:30
BAND_LOW, BAND_HIGH = 1.12, 1.16
CAP, BASE_LEVELS = 30, 20
TP_PCT = 0.001
ATR_N, ATR_MULT = 14, 2.0

LIVE_START = datetime(2026, 8, 6, tzinfo=timezone.utc)
# Výpadok live spojenia: bot neotváral, TP u brokera ďalej plnili.
OUTAGE = (datetime(2026, 9, 5, tzinfo=timezone.utc).timestamp(),
          datetime(2026, 9, 9, 9, 30, tzinfo=timezone.utc).timestamp())
SWAP_AUTO_FROM = datetime(2026, 9, 3, tzinfo=timezone.utc).timestamp()


def steps_live(t: float) -> tuple[float, float]:
    """(krok_long, krok_short) tak, ako reálne bežali — z odstupov vstupov
    v live DB: do 2. 9. lab G2B, potom swapová automatika."""
    return (0.00225, 0.0015) if t < SWAP_AUTO_FROM else (0.0015, 0.0010)


# --- dáta -------------------------------------------------------------------

def _fetch_day(d: date) -> list[tuple]:
    cache = DATA / "m1" / f"{d:%Y%m%d}.bi5"
    today = datetime.now(timezone.utc).date()
    if cache.exists():
        raw = cache.read_bytes()
    else:
        url = DUKA.format(y=d.year, m=d.month - 1, d=d.day)
        for attempt in range(10):
            try:
                raw = urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=60).read()
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raw = b""
                    break
                err = exc
            except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
                err = exc
            time.sleep(min(60, 2 ** attempt))   # Dukascopy pri náraze resetuje
        else:
            if d.weekday() != 5:
                raise RuntimeError(f"{d}: {err}")
            raw = b""                       # sobota: FX sa neobchoduje
        if d < today:                      # dnešok je neúplný, necachujeme
            cache.write_bytes(raw)
    if not raw:
        return []
    b = lzma.decompress(raw)
    base = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
    out = []
    for i in range(len(b) // 24):
        t, o, c, lo, hi, v = struct.unpack(">5if", b[i * 24:(i + 1) * 24])
        if v <= 0:                          # víkendová výplň bez obchodov
            continue
        out.append((int(base + t), o / 1e5, hi / 1e5, lo / 1e5, c / 1e5))
    return out


def load_m5(start: date, end: date) -> list[tuple]:
    """M5 sviečky (t, o, h, l, c) z Dukascopy M1 BID, s cache v data/."""
    out_csv = DATA / f"EURUSD_M5_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    if out_csv.exists():
        with open(out_csv) as f:
            return [(int(r[0]), *map(float, r[1:])) for r in csv.reader(f)]
    (DATA / "m1").mkdir(parents=True, exist_ok=True)
    days = [start + timedelta(i) for i in range((end - start).days + 1)]
    with ThreadPoolExecutor(2) as ex:
        m1 = [row for day in ex.map(_fetch_day, days) for row in day]
    m1.sort()
    bars: list[list] = []
    for t, o, h, l, c in m1:
        b = t - t % 300
        if bars and bars[-1][0] == b:
            cur = bars[-1]
            cur[2] = max(cur[2], h)
            cur[3] = min(cur[3], l)
            cur[4] = c
        else:
            bars.append([b, o, h, l, c])
    with open(out_csv, "w", newline="") as f:
        csv.writer(f).writerows(bars)
    return [tuple(b) for b in bars]


# --- účtovníctvo ------------------------------------------------------------

@dataclass
class Pos:
    side: str
    entry: float
    tp: float
    bar: int
    level: int | None = None


@dataclass
class Book:
    name: str
    capital: float
    open: list[Pos] = field(default_factory=list)
    realized: float = 0.0
    comm: float = 0.0
    swap: float = 0.0
    cycles: dict = field(default_factory=lambda: {"long": 0, "short": 0})
    opened: dict = field(default_factory=lambda: {"long": 0, "short": 0})
    max_open: dict = field(default_factory=lambda: {"long": 0, "short": 0, "spolu": 0})
    min_float: float = 0.0
    min_eq: float = 0.0
    req100: float = 0.0
    req200: float = 0.0
    min_ml: float = math.inf
    last_float: float = 0.0
    daily: dict = field(default_factory=dict)   # deň -> [výsledok, min marža, L, S]
    _last_t: float | None = None

    def open_pos(self, p: Pos) -> None:
        self.open.append(p)
        self.opened[p.side] += 1
        self.comm += COMMISSION

    def close_pos(self, p: Pos, price: float) -> None:
        self.open.remove(p)
        diff = price - p.entry if p.side == "long" else p.entry - price
        self.realized += diff * QTY / price
        self.cycles[p.side] += 1

    def rollover(self, t: float) -> None:
        """Swap za rollovery (21:00 UTC po–pi, streda 3×) od minulej sviečky."""
        prev, self._last_t = self._last_t, t
        if prev is None:
            return
        d = datetime.fromtimestamp(prev, timezone.utc).replace(
            hour=21, minute=0, second=0, microsecond=0)
        if d.timestamp() <= prev:
            d += timedelta(days=1)
        while d.timestamp() <= t:
            if d.weekday() < 5 and self.open:
                long_pa, short_pa = funding_rates_pa(d.strftime("%Y-%m-%d"))
                mult = 3 if d.weekday() == 2 else 1
                for p in self.open:
                    pa = -long_pa if p.side == "long" else short_pa
                    self.swap += pa / 100 / 365 * QTY * mult
            d += timedelta(days=1)

    def mark(self, bid: float, t: float | None = None) -> None:
        ask = bid + SPREAD
        fl = sum((bid - p.entry) * QTY / bid if p.side == "long"
                 else (p.entry - ask) * QTY / ask for p in self.open)
        self.last_float = fl
        nl = sum(p.side == "long" for p in self.open)
        ns = len(self.open) - nl
        self.max_open["long"] = max(self.max_open["long"], nl)
        self.max_open["short"] = max(self.max_open["short"], ns)
        self.max_open["spolu"] = max(self.max_open["spolu"], nl + ns)
        eq = self.realized - self.comm + self.swap + fl
        used = MARGIN_PER_POS * (nl + ns)
        self.min_float = min(self.min_float, fl)
        self.min_eq = min(self.min_eq, eq)
        self.req100 = max(self.req100, used - eq)
        self.req200 = max(self.req200, 2 * used - eq)
        ml = (self.capital + eq) / used * 100 if used else None
        if ml is not None:
            self.min_ml = min(self.min_ml, ml)
        if t is not None:
            d = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
            row = self.daily.setdefault(d, [eq, ml, nl, ns])
            row[0], row[2], row[3] = eq, nl, ns
            if ml is not None and (row[1] is None or ml < row[1]):
                row[1] = ml

    def summary(self) -> dict:
        net = self.realized - self.comm + self.swap
        nl = sum(p.side == "long" for p in self.open)
        return {
            "stratégia": self.name,
            "cyklov": self.cycles["long"] + self.cycles["short"],
            "cyklov L/S": f'{self.cycles["long"]}/{self.cycles["short"]}',
            "hrubý zisk": round(self.realized, 2),
            "provízie": round(-self.comm, 2),
            "swap": round(self.swap, 2),
            "realizovaný čistý": round(net, 2),
            "float na konci": round(self.last_float, 2),
            "výsledok vč. floatu": round(net + self.last_float, 2),
            "otvorené na konci L/S": f"{nl}/{len(self.open) - nl}",
            "max otvorených L/S": f'{self.max_open["long"]}/{self.max_open["short"]}',
            "max otvorených spolu": self.max_open["spolu"],
            "najhlbší float": round(self.min_float, 2),
            "kapitál pre maržu ≥100 %": round(self.req100, 0),
            "kapitál pre maržu ≥200 %": round(self.req200, 0),
            f"min. marža pri {self.capital:,.0f}": (
                f"{self.min_ml:.0f} %" if self.min_ml < math.inf else "—"),
        }


# --- stratégie --------------------------------------------------------------

def sim_g2b(bars, steps, capital, outage=None, name="G2B-CT",
            anchor: str = "extreme") -> Book:
    """Verná kópia Grid25.on_bar + toho, ako ho volá bot_ctrader.

    anchor="extreme" — kotva je bežiace max/min (tak bot bežal do #16),
    anchor="entry"   — kotva je posledný vstup, mení sa len pri vstupe
                       a pri vyprázdnení strany (#16, 6. 10. 2026),
    anchor="hybrid"  — ako "entry", ale prázdna strana sleduje extrém."""
    bk = Book(name, capital)
    ref_l = ref_s = None
    last_l = last_s = 0.0
    atr = prev_close = None
    for i, (t, o, h, l, c) in enumerate(bars):
        bk.rollover(t)
        # TP u brokera — plnia aj počas výpadku bota
        for p in [p for p in bk.open if p.bar < i]:
            if p.side == "long" and h >= p.tp:
                bk.close_pos(p, p.tp)
                if not any(q.side == "long" for q in bk.open):
                    ref_l = p.tp
            elif p.side == "short" and l + SPREAD <= p.tp:
                bk.close_pos(p, p.tp)
                if not any(q.side == "short" for q in bk.open):
                    ref_s = p.tp
        # ATR(14) Wilder na M5, ako v bote
        pc = prev_close if prev_close is not None else o
        tr = max(h - l, abs(h - pc), abs(l - pc))
        atr = tr if atr is None else atr * (ATR_N - 1) / ATR_N + tr / ATR_N
        prev_close = c

        if outage and outage[0] <= t < outage[1]:
            bk.mark(c, t)
            continue                       # bot nebeží: ani kotva, ani vstup

        step_l, step_s = steps(t)
        n_l = sum(p.side == "long" for p in bk.open)
        n_s = len(bk.open) - n_l
        if anchor == "extreme":
            ref_l = max(ref_l or c, h)
            ref_s = min(ref_s or c, l)
        elif anchor == "hybrid":
            # prázdna strana sleduje extrém (inak zamrzne, keď trh odíde),
            # strana s pozíciou drží kotvu na poslednom vstupe (odstup ≥ krok)
            ref_l = max(ref_l or c, h) if n_l == 0 else ref_l
            ref_s = min(ref_s or c, l) if n_s == 0 else ref_s
        else:
            ref_l = c if ref_l is None else ref_l
            ref_s = c if ref_s is None else ref_s

        if c < BAND_HIGH and n_l < CAP:
            drop = ref_l - c
            unlock = n_l < BASE_LEVELS or abs(c - last_l) > ATR_MULT * atr
            if drop >= max(ref_l * step_l, ATR_MULT * atr) and unlock:
                gap = int(drop / (ref_l * step_l)) >= 2
                tp = round(c * (1 + step_l) if gap else c * (1 + TP_PCT), 5)
                fill = c + SPREAD
                bk.open_pos(Pos("long", fill, tp, i))
                last_l = ref_l = fill
        if c > BAND_LOW and n_s < CAP:
            rise = c - ref_s
            unlock = n_s < BASE_LEVELS or abs(c - last_s) > ATR_MULT * atr
            if rise >= ref_s * step_s and unlock:
                gap = int(rise / (ref_s * step_s)) >= 2
                tp = round(c * (1 - step_s) if gap else c * (1 - TP_PCT), 5)
                bk.open_pos(Pos("short", c, tp, i))
                last_s = ref_s = c
        bk.mark(c, t)
    return bk


def sim_ladder(bars, step, capital, outage=None, name=None) -> Book:
    """Pevná mriežka L_k = (1+step)^k, limitky u brokera, bot len prezbrojuje.

    Počas výpadku bota plnia len limitky založené pred výpadkom; úroveň
    zavretá na TP počas výpadku sa znovu založí až po návrate bota."""
    bk = Book(name or f"LADDER {step:.2%}", capital)
    lg = math.log1p(step)
    lvl = lambda k: (1 + step) ** k                      # noqa: E731
    longs: dict[int, Pos] = {}
    shorts: dict[int, Pos] = {}
    prev_c = None
    frozen = None                                       # kurz na začiatku výpadku
    for i, (t, o, h, l, c) in enumerate(bars):
        bk.rollover(t)
        done_l, done_s = set(), set()
        for k, p in list(longs.items()):
            if p.bar < i and h >= p.tp:
                bk.close_pos(p, p.tp)
                del longs[k]
                done_l.add(k)
        for k, p in list(shorts.items()):
            if p.bar < i and l + SPREAD <= p.tp:
                bk.close_pos(p, p.tp)
                del shorts[k]
                done_s.add(k)
        if prev_c is None:
            prev_c = c
            bk.mark(c, t)
            continue

        down = outage and outage[0] <= t < outage[1]
        if down and frozen is None:
            frozen = (prev_c, set(), set())
        elif not down:
            frozen = None
        if frozen:                                       # bez bota sa neprezbrojuje
            frozen[1].update(done_l)
            frozen[2].update(done_s)
        arm_px = frozen[0] if frozen else prev_c

        # long: limitky na úrovniach pod kurzom, plnia od najvyššej
        k = math.ceil(math.log(arm_px + SPREAD) / lg) - 1
        for _ in range(400):
            L = lvl(k)
            if L < l + SPREAD or len(longs) >= CAP:
                break
            if (k not in longs and k not in done_l and L < BAND_HIGH
                    and not (frozen and k in frozen[1])):
                p = Pos("long", min(L, o + SPREAD), lvl(k + 1), i, k)
                longs[k] = p
                bk.open_pos(p)
            k -= 1
        # short: limitky nad kurzom, plnia od najnižšej
        k = math.floor(math.log(arm_px) / lg) + 1
        for _ in range(400):
            L = lvl(k)
            if L > h or len(shorts) >= CAP:
                break
            if (k not in shorts and k not in done_s and L > BAND_LOW
                    and not (frozen and k in frozen[2])):
                p = Pos("short", max(L, o), lvl(k - 1), i, k)
                shorts[k] = p
                bk.open_pos(p)
            k += 1
        prev_c = c
        bk.mark(c, t)
    return bk


# --- výstup -----------------------------------------------------------------

def table(books: list[Book], title: str) -> None:
    rows = [b.summary() for b in books]
    keys = list(rows[0])
    w0 = max(len(k) for k in keys) + 2
    ws = [max(12, len(str(r["stratégia"])) + 2) for r in rows]
    print(f"\n{title}\n" + "─" * (w0 + sum(ws)))
    for k in keys:
        print(f"{k:<{w0}}" + "".join(f"{str(r[k]):>{w}}" for r, w in zip(rows, ws)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live-only", action="store_true")
    ap.add_argument("--json", help="ulož denné série live obdobia (pre graf)")
    ap.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat())
    args = ap.parse_args()
    end = date.fromisoformat(args.end)

    bars = load_m5(LIVE_START.date(), end)
    print(f"Live obdobie: {len(bars)} M5 sviečok "
          f"{datetime.fromtimestamp(bars[0][0], timezone.utc):%Y-%m-%d} → "
          f"{datetime.fromtimestamp(bars[-1][0], timezone.utc):%Y-%m-%d %H:%M}")
    live = [
        sim_g2b(bars, steps_live, 15_200, OUTAGE, "G2B-CT (ako bežal)"),
        sim_g2b(bars, steps_live, 15_200, OUTAGE, "G2B kotva=vstup (#16)",
                anchor="entry"),
        sim_g2b(bars, steps_live, 15_200, OUTAGE, "G2B hybrid",
                anchor="hybrid"),
        sim_ladder(bars, 0.001, 15_200, OUTAGE, "MRIEŽKA 0,10 %"),
        sim_ladder(bars, 0.0015, 15_200, OUTAGE, "MRIEŽKA 0,15 %"),
    ]
    table(live, "A) OD SPUSTENIA LIVE BOTA (6. 8. 2026 → dnes), kapitál 15 200")
    if args.json:
        import json
        Path(args.json).write_text(json.dumps({
            b.name: {"daily": b.daily, "summary": b.summary()} for b in live
        }, ensure_ascii=False, default=str))

    if args.live_only:
        return 0

    start = end - timedelta(days=730)
    bars2 = load_m5(start, end)
    print(f"\nLab: {len(bars2)} M5 sviečok {start} → {end}")
    table([
        sim_g2b(bars2, lambda t: (0.00225, 0.0015), 15_200, None, "G2B lab"),
        sim_g2b(bars2, lambda t: (0.0015, 0.0010), 15_200, None, "G2B dnešné kroky"),
        sim_g2b(bars2, lambda t: (0.0015, 0.0010), 15_200, None, "G2B #16 dnešné",
                anchor="entry"),
        sim_g2b(bars2, lambda t: (0.0015, 0.0010), 15_200, None, "G2B hybrid dnešné",
                anchor="hybrid"),
        sim_ladder(bars2, 0.001, 15_200, None, "MRIEŽKA 0,10 %"),
        sim_ladder(bars2, 0.0015, 15_200, None, "MRIEŽKA 0,15 %"),
    ], "B) LAB — 2 ROKY HISTÓRIE, kapitál 15 200")
    return 0


if __name__ == "__main__":
    sys.exit(main())
