"""Pattern miner — systematické hľadanie sviečkových sekvencií na EURUSD.

Reprezentácia sviečky (3 ortogonálne osi → 27 symbolov):
* smer:    u (up) / d (down) / j (doji — telo ≤ 20 % range)
* telo:    s / m / l  (telo voči ATR(14) daného TF: < 0,33× / < 1× / ≥ 1×)
* close:   t / c / b  (horná / stredná / dolná tretina range)
Symbol = 3 znaky, napr. "ult" = up, veľké telo, close hore.

Timeframy: H1, H4, D1 (časovo zarovnané buckety; M5 vynechané — šum).
Sekvencie dĺžky 2–4 po sebe idúcich symbolov. Outcome = návrat ďalšej
sviečky a ďalších 2 sviečok (close→close, bps). Edge sa hodnotí PO
NÁKLADOCH: |priemer| − 1,5 bps (round-trip ECN).

Štatistická disciplína:
* mining LEN 2013–2022; validácia 2023–2026
* min. 200 výskytov na mining sete
* jeden test = (pattern, TF, horizont); jednovýberový t-test priemeru
  voči nule (p cez normálnu aproximáciu — pri n ≥ 200 v poriadku)
* Benjamini–Hochberg FDR α = 0,05 GLOBÁLNE cez všetky TF a horizonty
* preživší musí mať: BH pass + čistý edge > 0 + validácia rovnakého
  znamienka s ≥ 60 % pôvodnej sily (val n ≥ 30)
* kontrolná vzorka: koľko patternov by „prešlo“ bez disciplíny
  (celé dáta, surové p < 0,05, bez validácie) — rozsah ilúzie

Pozn.: outcomes po sebe idúcich výskytov sú rôzne bary (žiadne zdieľanie
outcome baru), ale zhlukovanie výskytov mierne nafukuje efektívne n —
výsledné p ber ako optimistickú hranicu, nie presnú pravdepodobnosť.

Beh: python3 pattern_miner.py
Výstup: tabuľka preživších + funnel + data/backtest_v2/pattern_survivors.csv
"""

from __future__ import annotations

import csv
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from backtest_v2 import Bars
from study_phase1 import merged_h1

ROOT = Path(__file__).resolve().parent
OUT_CSV = ROOT / "data" / "backtest_v2" / "pattern_survivors.csv"

COST_BPS = 1.5              # round-trip ECN
MIN_N_MINE = 200
MIN_N_VAL = 30
ALPHA = 0.05
RETENTION = 0.60            # validácia musí udržať ≥ 60 % sily
SPLIT_TS = datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------- dáta
def bucket_bars(h1: Bars, seconds: int) -> Bars:
    """Časovo zarovnané buckety (H4 = 14400 s, D1 = 86400 s)."""
    t, o, h, l, c = [], [], [], [], []
    cur = None
    for i in range(len(h1.t)):
        k = int(h1.t[i] // seconds)
        if k != cur:
            cur = k
            t.append(k * seconds)
            o.append(h1.open[i]); h.append(h1.high[i])
            l.append(h1.low[i]); c.append(h1.close[i])
        else:
            h[-1] = max(h[-1], h1.high[i])
            l[-1] = min(l[-1], h1.low[i])
            c[-1] = h1.close[i]
    return Bars(f"TF{seconds}", np.array(t), np.array(o), np.array(h),
                np.array(l), np.array(c))


def atr_wilder_arr(b: Bars, n: int = 14) -> np.ndarray:
    pc = np.concatenate(([b.close[0]], b.close[:-1]))
    tr = np.maximum(b.high - b.low,
                    np.maximum(np.abs(b.high - pc), np.abs(b.low - pc)))
    atr = np.full(len(b.t), np.nan)
    if len(b.t) <= n:
        return atr
    atr[n - 1] = tr[:n].mean()
    for i in range(n, len(tr)):
        atr[i] = atr[i - 1] * (n - 1) / n + tr[i] / n
    return atr


def symbolize(b: Bars, atr: np.ndarray) -> list[str | None]:
    """27-znaková abeceda [udj][sml][tcb]; None počas ATR warmupu."""
    out: list[str | None] = []
    for i in range(len(b.t)):
        a = atr[i]
        if np.isnan(a) or a <= 0:
            out.append(None)
            continue
        o, h, l, c = b.open[i], b.high[i], b.low[i], b.close[i]
        rng = h - l
        body = abs(c - o)
        d = "j" if (rng > 0 and body <= 0.2 * rng) or rng == 0 else \
            ("u" if c > o else "d")
        s = "s" if body < 0.33 * a else ("m" if body < 1.0 * a else "l")
        if rng <= 0:
            pos = "c"
        else:
            q = (c - l) / rng
            pos = "b" if q < 1 / 3 else ("t" if q > 2 / 3 else "c")
        out.append(d + s + pos)
    return out


# ---------------------------------------------------------------- štatistika
def one_sample(x: np.ndarray) -> tuple[float, float, float]:
    """(mean, t, p) — jednovýberový test voči 0, p normálnou aproximáciou."""
    n = len(x)
    m = float(x.mean())
    sd = float(x.std(ddof=1))
    if sd == 0 or n < 3:
        return m, 0.0, 1.0
    t = m / (sd / math.sqrt(n))
    p = math.erfc(abs(t) / math.sqrt(2))
    return m, t, p


def bh_fdr(pvals: list[float], alpha: float) -> list[bool]:
    """Benjamini–Hochberg: vráti pass/fail v pôvodnom poradí."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    passed = [False] * m
    k_max = -1
    for rank, idx in enumerate(order, start=1):
        if pvals[idx] <= rank / m * alpha:
            k_max = rank
    for rank, idx in enumerate(order, start=1):
        if rank <= k_max:
            passed[idx] = True
    return passed


def bh_adj(pvals: list[float]) -> list[float]:
    """BH-adjustované p (monotónne)."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    prev = 1.0
    for rank in range(m, 0, -1):
        idx = order[rank - 1]
        val = min(prev, pvals[idx] * m / rank)
        adj[idx] = val
        prev = val
    return adj


# ---------------------------------------------------------------- mining
def collect(tf_name: str, b: Bars):
    atr = atr_wilder_arr(b)
    syms = symbolize(b, atr)
    n = len(b.t)
    r1 = np.full(n, np.nan)
    r2 = np.full(n, np.nan)
    r1[:-1] = (b.close[1:] / b.close[:-1] - 1) * 1e4
    r2[:-2] = (b.close[2:] / b.close[:-2] - 1) * 1e4
    years = np.array([datetime.fromtimestamp(int(t), tz=timezone.utc).year
                      for t in b.t])
    mine_mask = b.t < SPLIT_TS

    occ: dict[tuple, list[int]] = {}
    for L in (2, 3, 4):
        for i in range(L - 1, n - 2):
            window = syms[i - L + 1:i + 1]
            if any(s is None for s in window):
                continue
            occ.setdefault((L, "·".join(window)), []).append(i)
    return {"tf": tf_name, "occ": occ, "r": {1: r1, 2: r2},
            "years": years, "mine": mine_mask}


def main() -> int:
    print("PATTERN MINER — pripravujem dáta…", flush=True)
    h1 = merged_h1()
    frames = [("H1", h1), ("H4", bucket_bars(h1, 14400)),
              ("D1", bucket_bars(h1, 86400))]
    datasets = []
    for name, b in frames:
        d = collect(name, b)
        datasets.append(d)
        print(f"  {name}: {len(b.t):,} sviečok, {len(d['occ']):,} unikátnych "
              f"sekvencií (2–4)", flush=True)

    # --- testy na mining sete -------------------------------------------
    tests = []          # dict: tf, pattern, L, horizon, idxs, n, mean, p
    for d in datasets:
        for (L, pat), idxs in d["occ"].items():
            idxs = np.asarray(idxs)
            mi = idxs[d["mine"][idxs]]
            if len(mi) < MIN_N_MINE:
                continue
            for hz in (1, 2):
                x = d["r"][hz][mi]
                x = x[~np.isnan(x)]
                if len(x) < MIN_N_MINE:
                    continue
                mean, t, p = one_sample(x)
                tests.append({"tf": d["tf"], "pat": pat, "L": L, "hz": hz,
                              "idxs": idxs, "d": d, "n_mine": len(x),
                              "mean_mine": mean, "p": p})
    m_tests = len(tests)
    print(f"\nTestovacia rodina: {m_tests:,} testov "
          f"(pattern × TF × horizont s n ≥ {MIN_N_MINE})")
    print(f"Očakávané falošné pozitíva bez korekcie: ~{0.05 * m_tests:.0f}")

    pvals = [t_["p"] for t_ in tests]
    passed = bh_fdr(pvals, ALPHA)
    padj = bh_adj(pvals)

    # --- validácia preživších BH ----------------------------------------
    survivors = []
    bh_count = 0
    for ok, pa, t_ in zip(passed, padj, tests):
        edge_net_mine = abs(t_["mean_mine"]) - COST_BPS
        if not ok or edge_net_mine <= 0:
            continue
        bh_count += 1
        d = t_["d"]
        idxs = t_["idxs"]
        va = idxs[~d["mine"][idxs]]
        xv = d["r"][t_["hz"]][va]
        xv = xv[~np.isnan(xv)]
        if len(xv) < MIN_N_VAL:
            status = "nedosť validačných dát"
            keep = False
            vm = float("nan")
        else:
            vm = float(xv.mean())
            same_sign = vm * t_["mean_mine"] > 0
            keep = same_sign and abs(vm) >= RETENTION * abs(t_["mean_mine"])
            status = "OK" if keep else "nezvalidoval sa"
        if keep:
            # stabilita po rokoch (celé dáta)
            yr_means = []
            for y in sorted(set(d["years"][idxs])):
                sel = idxs[d["years"][idxs] == y]
                xr = d["r"][t_["hz"]][sel]
                xr = xr[~np.isnan(xr)]
                if len(xr) >= 10:
                    yr_means.append((y, float(xr.mean())))
            same = sum(1 for _, mm in yr_means
                       if mm * t_["mean_mine"] > 0)
            survivors.append({**{k: t_[k] for k in
                                 ("tf", "pat", "L", "hz", "n_mine",
                                  "mean_mine")},
                              "p_adj": pa, "n_val": len(xv), "mean_val": vm,
                              "retention": abs(vm) / abs(t_["mean_mine"]),
                              "years_ok": f"{same}/{len(yr_means)}"})

    # --- kontrolná vzorka: bez disciplíny --------------------------------
    naive = 0
    for d in datasets:
        for (L, pat), idxs in d["occ"].items():
            idxs = np.asarray(idxs)
            if len(idxs) < MIN_N_MINE:
                continue
            for hz in (1, 2):
                x = d["r"][hz][idxs]
                x = x[~np.isnan(x)]
                if len(x) < MIN_N_MINE:
                    continue
                mean, t, p = one_sample(x)
                if p < 0.05 and abs(mean) - COST_BPS > 0:
                    naive += 1

    # --- výstup -----------------------------------------------------------
    print("\n" + "=" * 74)
    print("FUNNEL DISCIPLÍNY")
    print(f"  bez korekcie a delenia (celé dáta, p<0,05, čistý edge>0): "
          f"{naive} patternov")
    print(f"  po BH-FDR na mining sete (α={ALPHA}) + čistý edge>0:        "
          f"{bh_count}")
    print(f"  po validácii 2023–2026 (≥{RETENTION:.0%} sily, rovnaké "
          f"znamienko): {len(survivors)}")

    print("\n" + "=" * 74)
    if not survivors:
        print("PREŽIVŠÍ: ŽIADNI — po plnej disciplíne neprežil ani jeden "
              "pattern.")
    else:
        print(f"PREŽIVŠÍ ({len(survivors)}):")
        hdr = (f"  {'TF':<4}{'pattern':<18}{'hz':>3}{'n_mine':>8}"
               f"{'edge bps':>9}{'čistý':>7}{'p_adj':>9}{'n_val':>7}"
               f"{'val bps':>9}{'sila':>6}{'roky':>7}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for s in sorted(survivors, key=lambda x: -(abs(x["mean_val"]) - COST_BPS)):
            print(f"  {s['tf']:<4}{s['pat']:<18}{s['hz']:>3}{s['n_mine']:>8}"
                  f"{s['mean_mine']:>+9.2f}{abs(s['mean_mine']) - COST_BPS:>7.2f}"
                  f"{s['p_adj']:>9.4f}{s['n_val']:>7}{s['mean_val']:>+9.2f}"
                  f"{s['retention']:>6.0%}{s['years_ok']:>7}")
        OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT_CSV, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["tf", "pattern", "len", "horizon", "n_mine",
                        "edge_bps_mine", "edge_net_mine", "p_adj", "n_val",
                        "edge_bps_val", "retention", "years_consistent"])
            for s in survivors:
                w.writerow([s["tf"], s["pat"], s["L"], s["hz"], s["n_mine"],
                            round(s["mean_mine"], 3),
                            round(abs(s["mean_mine"]) - COST_BPS, 3),
                            round(s["p_adj"], 5), s["n_val"],
                            round(s["mean_val"], 3),
                            round(s["retention"], 3), s["years_ok"]])
        print(f"\nCSV: {OUT_CSV.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
