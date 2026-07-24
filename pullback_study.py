"""FÁZA 1 — PullbackStudy: deskriptívna analýza pullbackov po smerových
pohyboch EURUSD (bez obchodovania).

Definícia pohybu
----------------
Zigzag s reverzným prahom 0.15 %: noha zigzagu = smerový pohyb bez
medzipullbacku > 0.15 % (z konštrukcie). Kvalifikovaný pohyb má amplitúdu
≥ 0.45 % (3 grid kroky) a trvanie ≤ 4 h (kohorta A) resp. ≤ 24 h
(kohorta B ⊇ A). Extrémy sa merajú na high/low barov.

Meranie pullbacku (po extréme E nohy S→E)
-----------------------------------------
* dno = najhlbší protipohyb pred prvým návratom za E (pokračovanie)
  alebo za S (otočenie); cap okno = 5× trvanie pohybu, min 12 h, max 7 d.
* hĺbka v % pôvodného pohybu; ak sa vráti za S, zaznamená sa ≥ 100 %.
* čas do začiatku pullbacku = kým protipohyb od E prekročí 0.15 %;
  čas do dna = E → timestamp dna.
* výsledok: continued / reversed / timeout.

Delenia: seansa podľa času extrému E (Ázia 00–09, EÚ 09–15, US 15–22,
22–24 → Ázia; Europe/Bratislava), deň v týždni, news proxy (pohyb
obsahuje bar s range ≥ 4× ATR14 — historický kalendár nie je), veľkosť
(0.45–0.6 / 0.6–1.0 / > 1.0 %).

Dáta: Dukascopy H1 2013 → 2023-08 + IBKR M5 2023-08 → dnes (natívne
rozlíšenia; citlivosť detekcie na TF sa reportuje zvlášť na M5/H1/H4
agregátoch IBKR periódy).

Výstup: tabuľky na stdout, histogramy PNG + raw nohy do
data/backtest_v2/pullback_legs.csv, záver so subsetmi P(pullback≥38 %)
> 60 % (min n=50) vrátane testu stability pre-2025 vs 2025+.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from backtest_v2 import (IBKR_CSV, Bars, atr_wilder, load_dukascopy_h1,
                         load_ibkr_csv)

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "data" / "backtest_v2"
TZ = ZoneInfo("Europe/Bratislava")

REV_TH = 0.0015            # 0.15 % reverzný prah zigzagu / medzipullback
MIN_MOVE = 0.0045          # 0.45 % kvalifikačná amplitúda
WINDOW_A_H = 4.0
WINDOW_B_H = 24.0
NEWS_ATR_MULT = 4.0
CAP_MULT = 5.0             # cap okno sledovania pullbacku = 5× trvanie
CAP_MIN_H = 12.0
CAP_MAX_H = 24.0 * 7


@dataclass
class Leg:
    direction: int          # +1 up / -1 down
    t_start: float
    t_end: float
    p_start: float
    p_end: float
    size_pct: float
    dur_h: float
    resolution: str         # M5 | H1
    # pullback
    depth_pct: float = np.nan     # % pôvodného pohybu (≥100 = plný návrat)
    t_pb_start_h: float = np.nan
    t_pb_bottom_h: float = np.nan
    outcome: str = "timeout"      # continued | reversed | timeout
    session: str = ""
    weekday: int = 0
    news: bool = False
    bucket: str = ""


def zigzag_legs(b: Bars, resolution: str) -> list[Leg]:
    """Nohy zigzagu (high/low, reverzný prah REV_TH)."""
    h, l, t = b.high, b.low, b.t
    n = len(t)
    legs: list[Leg] = []

    # inicializácia: sleduj bežiace max aj min, kým sa neurčí prvý smer
    hi_p, hi_i = h[0], 0
    lo_p, lo_i = l[0], 0
    direction = 0
    start_p = start_i = ext_p = ext_i = None
    i = 0
    while i < n and direction == 0:
        if h[i] > hi_p:
            hi_p, hi_i = h[i], i
        if l[i] < lo_p:
            lo_p, lo_i = l[i], i
        if hi_p - l[i] > hi_p * REV_TH:
            direction = -1
            start_p, start_i = hi_p, hi_i
            ext_p, ext_i = l[i], i
        elif h[i] - lo_p > lo_p * REV_TH:
            direction = 1
            start_p, start_i = lo_p, lo_i
            ext_p, ext_i = h[i], i
        i += 1

    for j in range(i, n):
        if direction == 1:              # up-noha
            if h[j] > ext_p:
                ext_p, ext_i = h[j], j
            if ext_p - l[j] > ext_p * REV_TH:
                legs.append(Leg(1, t[start_i], t[ext_i], start_p, ext_p,
                                (ext_p - start_p) / start_p,
                                (t[ext_i] - t[start_i]) / 3600, resolution))
                start_p, start_i = ext_p, ext_i
                direction = -1
                ext_p, ext_i = l[j], j
        else:                           # down-noha
            if l[j] < ext_p:
                ext_p, ext_i = l[j], j
            if h[j] - ext_p > ext_p * REV_TH:
                legs.append(Leg(-1, t[start_i], t[ext_i], start_p, ext_p,
                                (start_p - ext_p) / start_p,
                                (t[ext_i] - t[start_i]) / 3600, resolution))
                start_p, start_i = ext_p, ext_i
                direction = 1
                ext_p, ext_i = h[j], j
    return [x for x in legs if x.size_pct >= MIN_MOVE and x.dur_h > 0]


def measure_pullback(leg: Leg, b: Bars, i_end: int) -> None:
    """Doplní pullback metriky nohy (in-place)."""
    E, S, d = leg.p_end, leg.p_start, leg.direction
    move = abs(E - S)
    cap_s = min(max(leg.dur_h * CAP_MULT, CAP_MIN_H), CAP_MAX_H) * 3600
    t_limit = leg.t_end + cap_s
    bottom = E
    t_bottom = leg.t_end
    t_start_pb = np.nan
    n = len(b.t)
    for j in range(i_end + 1, n):
        if b.t[j] > t_limit:
            break
        if d == 1:                               # up-noha → pullback nadol
            if b.low[j] < bottom:
                bottom, t_bottom = b.low[j], b.t[j]
            if np.isnan(t_start_pb) and E - b.low[j] > E * REV_TH:
                t_start_pb = b.t[j]
            if b.high[j] > E:                    # pokračovanie
                leg.outcome = "continued"
                break
            if b.low[j] < S:                     # plné otočenie
                leg.outcome = "reversed"
                break
        else:                                    # down-noha → pullback nahor
            if b.high[j] > bottom:
                bottom, t_bottom = b.high[j], b.t[j]
            if np.isnan(t_start_pb) and b.high[j] - E > E * REV_TH:
                t_start_pb = b.t[j]
            if b.low[j] < E:                     # pokračovanie
                leg.outcome = "continued"
                break
            if b.high[j] > S:                    # plné otočenie
                leg.outcome = "reversed"
                break
    depth = (E - bottom) if d == 1 else (bottom - E)
    leg.depth_pct = 100.0 * max(depth, 0.0) / move if move else np.nan
    leg.t_pb_start_h = (t_start_pb - leg.t_end) / 3600 if not np.isnan(t_start_pb) else np.nan
    leg.t_pb_bottom_h = (t_bottom - leg.t_end) / 3600


def annotate(leg: Leg, news_flag: bool) -> None:
    dt = datetime.fromtimestamp(leg.t_end, tz=TZ)
    hrs = dt.hour
    leg.session = "Azia" if (hrs < 9 or hrs >= 22) else \
        ("EU" if hrs < 15 else "US")
    leg.weekday = dt.weekday()
    leg.news = news_flag
    s = leg.size_pct
    leg.bucket = "0.45-0.6" if s < 0.006 else ("0.6-1.0" if s < 0.01 else ">1.0")


def stats_block(legs: list[Leg]) -> dict:
    if not legs:
        return {}
    d = np.array([x.depth_pct for x in legs])
    cont = sum(1 for x in legs if x.outcome == "continued")
    rev = sum(1 for x in legs if x.outcome == "reversed")
    tps = np.array([x.t_pb_start_h for x in legs if not np.isnan(x.t_pb_start_h)])
    tpb = np.array([x.t_pb_bottom_h for x in legs])
    return {
        "n": len(legs),
        "med": float(np.median(d)),
        "q1": float(np.percentile(d, 25)),
        "q3": float(np.percentile(d, 75)),
        "p38": 100.0 * float((d >= 38).mean()),
        "p50": 100.0 * float((d >= 50).mean()),
        "p62": 100.0 * float((d >= 62).mean()),
        "t_start": float(np.median(tps)) if len(tps) else np.nan,
        "t_bottom": float(np.median(tpb)),
        "cont": 100.0 * cont / len(legs),
        "rev": 100.0 * rev / len(legs),
    }


def print_split(title: str, groups: dict[str, list[Leg]]) -> None:
    print(f"\n--- {title} ---")
    hdr = (f"{'skupina':<12}{'n':>6}{'medián%':>9}{'Q1':>6}{'Q3':>7}"
           f"{'≥38%':>7}{'≥50%':>7}{'≥62%':>7}{'t_zač h':>9}{'t_dno h':>9}"
           f"{'pokr%':>7}{'otoč%':>7}")
    print(hdr)
    for name, legs in groups.items():
        s = stats_block(legs)
        if not s:
            continue
        print(f"{name:<12}{s['n']:>6}{s['med']:>9.1f}{s['q1']:>6.1f}"
              f"{s['q3']:>7.1f}{s['p38']:>7.1f}{s['p50']:>7.1f}"
              f"{s['p62']:>7.1f}{s['t_start']:>9.2f}{s['t_bottom']:>9.1f}"
              f"{s['cont']:>7.1f}{s['rev']:>7.1f}")


def aggregate(b: Bars, factor: int, name: str) -> Bars:
    n = (len(b.t) // factor) * factor
    t = b.t[:n].reshape(-1, factor)
    o = b.open[:n].reshape(-1, factor)
    h = b.high[:n].reshape(-1, factor)
    l = b.low[:n].reshape(-1, factor)
    c = b.close[:n].reshape(-1, factor)
    return Bars(name, t[:, 0], o[:, 0], h.max(axis=1), l.min(axis=1), c[:, -1])


def main() -> int:
    print("PULLBACK STUDY — načítavam dáta…", flush=True)
    ibkr = load_ibkr_csv(IBKR_CSV)
    duka = load_dukascopy_h1()
    cut = ibkr.t[0]
    m = duka.t < cut
    duka_pre = Bars("DUKA", duka.t[m], duka.open[m], duka.high[m],
                    duka.low[m], duka.close[m])

    all_legs: list[Leg] = []
    for b, res in ((duka_pre, "H1"), (ibkr, "M5")):
        atr = atr_wilder(b)
        legs = zigzag_legs(b, res)
        t_index = {int(t): i for i, t in enumerate(b.t)}
        news_bar = (b.high - b.low) >= NEWS_ATR_MULT * np.nan_to_num(atr, nan=np.inf)
        for leg in legs:
            i_end = t_index[int(leg.t_end)]
            i_start = t_index[int(leg.t_start)]
            measure_pullback(leg, b, i_end)
            annotate(leg, bool(news_bar[i_start:i_end + 1].any()))
        all_legs += legs
        print(f"  {res}: {len(legs)} kvalifikovaných pohybov "
              f"({datetime.fromtimestamp(b.t[0], tz=timezone.utc):%Y-%m}"
              f" → {datetime.fromtimestamp(b.t[-1], tz=timezone.utc):%Y-%m})",
              flush=True)

    coh_a = [x for x in all_legs if x.dur_h <= WINDOW_A_H]
    coh_b = [x for x in all_legs if x.dur_h <= WINDOW_B_H]

    for title, cohort in (("KOHORTA A (pohyb ≤ 4 h)", coh_a),
                          ("KOHORTA B (pohyb ≤ 24 h)", coh_b)):
        print(f"\n{'=' * 78}\n=== {title} ===")
        print_split("celkovo", {"všetko": cohort})
        print_split("seansa (koniec pohybu)", {
            s: [x for x in cohort if x.session == s]
            for s in ("Azia", "EU", "US")})
        print_split("deň v týždni", {
            d: [x for x in cohort if x.weekday == i]
            for i, d in enumerate(("Po", "Ut", "St", "Št", "Pi"))})
        print_split("news proxy", {
            "news": [x for x in cohort if x.news],
            "organic": [x for x in cohort if not x.news]})
        print_split("veľkosť pohybu", {
            b_: [x for x in cohort if x.bucket == b_]
            for b_ in ("0.45-0.6", "0.6-1.0", ">1.0")})
        print_split("smer", {
            "up": [x for x in cohort if x.direction == 1],
            "down": [x for x in cohort if x.direction == -1]})

    # --- citlivosť detekcie na rozlíšenie (IBKR perióda) --------------------
    print(f"\n{'=' * 78}\n=== CITLIVOSŤ NA TF (IBKR perióda, kohorta B) ===")
    for factor, nm in ((1, "M5"), (12, "H1"), (48, "H4")):
        bb = ibkr if factor == 1 else aggregate(ibkr, factor, nm)
        lg = [x for x in zigzag_legs(bb, nm) if x.dur_h <= WINDOW_B_H]
        for leg in lg:
            pass
        d = np.array([x.size_pct for x in lg])
        print(f"  {nm}: {len(lg)} pohybov, medián veľkosti "
              f"{100 * np.median(d):.2f} % (hrubšie TF prehliada "
              f"medzipullbacky → menej/dlhšie nohy)")

    # --- subsety s P(≥38) > 60 % + stabilita --------------------------------
    print(f"\n{'=' * 78}\n=== SUBSETY s P(pullback ≥ 38 %) > 60 %  (n ≥ 50) ===")
    def keyfn(x):
        return (x.session, x.bucket, x.news)
    combos: dict[tuple, list[Leg]] = {}
    for x in coh_b:
        combos.setdefault(("seansa", x.session), []).append(x)
        combos.setdefault(("bucket", x.bucket), []).append(x)
        combos.setdefault(("news", str(x.news)), []).append(x)
        combos.setdefault(("smer", "up" if x.direction == 1 else "down"), []).append(x)
        combos.setdefault(("seansa+bucket", f"{x.session}|{x.bucket}"), []).append(x)
        combos.setdefault(("bucket+news", f"{x.bucket}|{x.news}"), []).append(x)
    t2025 = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()
    found = 0
    for key, legs in sorted(combos.items()):
        s = stats_block(legs)
        if not s or s["n"] < 50 or s["p38"] <= 60:
            continue
        pre = stats_block([x for x in legs if x.t_end < t2025])
        post = stats_block([x for x in legs if x.t_end >= t2025])
        found += 1
        print(f"  {key[0]}={key[1]:<16} n={s['n']:>5}  P(≥38)={s['p38']:.1f} % "
              f"medián {s['med']:.1f} %  | stabilita: pre-2025 "
              f"{pre.get('p38', float('nan')):.1f} % (n={pre.get('n', 0)}), "
              f"2025+ {post.get('p38', float('nan')):.1f} % (n={post.get('n', 0)})")
    if not found:
        print("  (žiadny subset nespĺňa kritérium)")

    # --- histogramy + CSV ----------------------------------------------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
    for ax, (nm, coh) in zip(axes, (("A (≤4h)", coh_a), ("B (≤24h)", coh_b))):
        d = np.clip([x.depth_pct for x in coh], 0, 120)
        ax.hist(d, bins=48, edgecolor="black", lw=0.3)
        for lv in (38, 50, 62, 100):
            ax.axvline(lv, color="red", ls="--", lw=0.8)
        ax.set_title(f"Hĺbka pullbacku — kohorta {nm} (n={len(coh)})")
        ax.set_xlabel("% pôvodného pohybu")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "pullback_depth_hist.png", dpi=110)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    for ax, b_ in zip(axes, ("0.45-0.6", "0.6-1.0", ">1.0")):
        d = np.clip([x.depth_pct for x in coh_b if x.bucket == b_], 0, 120)
        ax.hist(d, bins=36, edgecolor="black", lw=0.3)
        ax.axvline(38, color="red", ls="--", lw=0.8)
        ax.set_title(f"veľkosť {b_} % (n={len(d)})")
        ax.set_xlabel("% pôvodného pohybu")
    fig.suptitle("Hĺbka pullbacku podľa veľkosti pohybu (kohorta B)")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "pullback_by_size.png", dpi=110)
    plt.close(fig)

    with open(OUT_DIR / "pullback_legs.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_start", "t_end", "direction", "size_pct", "dur_h",
                    "resolution", "depth_pct", "t_pb_start_h", "t_pb_bottom_h",
                    "outcome", "session", "weekday", "news", "bucket"])
        for x in all_legs:
            w.writerow([datetime.fromtimestamp(x.t_start, tz=timezone.utc).isoformat(),
                        datetime.fromtimestamp(x.t_end, tz=timezone.utc).isoformat(),
                        x.direction, round(100 * x.size_pct, 3),
                        round(x.dur_h, 2), x.resolution,
                        round(x.depth_pct, 1), round(x.t_pb_start_h, 2),
                        round(x.t_pb_bottom_h, 2), x.outcome, x.session,
                        x.weekday, int(x.news), x.bucket])
    print(f"\nRaw nohy: data/backtest_v2/pullback_legs.csv "
          f"({len(all_legs)} pohybov) + 2 histogramy PNG.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
