#!/usr/bin/env python3
"""Zloží H1 dataset 2013–2026 z dukascopy H1 a IBKR M5.

G2B sa nedá testovať na denných baroch: podmienka max(krok, 2×ATR) na
long strane je kalibrovaná na intradenný ATR. Na D1 je medián 2×ATR
1,61 % proti kroku 0,225 %, takže longy sa nespustia takmer nikdy a
z obojsmerného gridu ostane short-only stratégia. Na H1 je 2×ATR 0,241 %,
teda porovnateľné s krokom, a obe strany žijú.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "data"
OUT = HERE / "data" / "eurusd_h1.csv"


def from_dukascopy() -> dict[int, tuple]:
    out: dict[int, tuple] = {}
    with (SRC / "dukascopy_EURUSD_H1.csv").open() as f:
        for r in csv.DictReader(f):
            ep = int(float(r["epoch"])) // 3600 * 3600
            out[ep] = (float(r["open"]), float(r["high"]),
                       float(r["low"]), float(r["close"]))
    return out


def from_ibkr_m5() -> dict[int, tuple]:
    """M5 → H1 agregácia (open prvého, max/min, close posledného)."""
    buckets: dict[int, list] = {}
    with (SRC / "ibkr_EURUSD_M5.csv").open() as f:
        for r in csv.DictReader(f):
            try:
                t = dt.datetime.fromisoformat(r["date"])
            except ValueError:
                continue
            ep = int(t.timestamp()) // 3600 * 3600
            buckets.setdefault(ep, []).append(
                (float(r["open"]), float(r["high"]),
                 float(r["low"]), float(r["close"])))
    out = {}
    for ep, rows in buckets.items():
        out[ep] = (rows[0][0], max(x[1] for x in rows),
                   min(x[2] for x in rows), rows[-1][3])
    return out


def main() -> None:
    duka = from_dukascopy()
    ibkr = from_ibkr_m5()
    # Prekryv 2023-08…2023-12 riešime v prospech dukascopy (natívne H1).
    merged = dict(ibkr)
    merged.update(duka)
    rows = []
    for ep in sorted(merged):
        o, h, l, c = merged[ep]
        d = dt.datetime.fromtimestamp(ep, dt.timezone.utc)
        rows.append((d.strftime("%Y-%m-%d %H:%M"), o, h, l, c))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close"])
        w.writerows(rows)
    print(f"eurusd_h1.csv: {len(rows)} barov, {rows[0][0]} → {rows[-1][0]}")
    print(f"  dukascopy {len(duka)}, ibkr {len(ibkr)}, po zlúčení {len(merged)}")


if __name__ == "__main__":
    main()
