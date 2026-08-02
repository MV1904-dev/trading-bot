#!/usr/bin/env python3
"""Stiahne dáta pre režimový test: EURUSD denné + ECB a Fed sadzby.

Všetko sú verejné zdroje s dennou frekvenciou a hodnotou známou v čase t,
takže sa dajú použiť v rozhodovacej logike bez look-ahead.

FRED je z niektorých sietí nedostupný — ak zlyhá priamo, skús ho stiahnuť
cez server a súbor prilož ručne (viď --help).
"""

from __future__ import annotations

import csv
import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data"
UA = {"User-Agent": "Mozilla/5.0"}


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_eurusd() -> None:
    """Denné EURUSD zo Yahoo. Vracia OHLC, čo grid potrebuje na high/low."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X"
           "?period1=1041379200&period2=1790000000&interval=1d")
    d = json.loads(_get(url))
    res = d["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = []
    for i, ts in enumerate(res["timestamp"]):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        day = datetime.fromtimestamp(ts, timezone.utc).date()
        rows.append((day.isoformat(), o, h, l, c))
    rows.sort()
    with (OUT / "eurusd_d.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close"])
        w.writerows(rows)
    print(f"eurusd_d.csv: {len(rows)} barov, {rows[0][0]} → {rows[-1][0]}")


def fetch_ecb() -> None:
    """ECB depo sadzba (deposit facility), denná, od 1999."""
    url = ("https://data-api.ecb.europa.eu/service/data/FM/"
           "D.U2.EUR.4F.KR.DFR.LEV?format=csvdata&detail=dataonly")
    text = _get(url).decode()
    rows = []
    rd = csv.DictReader(text.splitlines())
    for r in rd:
        try:
            rows.append((r["TIME_PERIOD"], float(r["OBS_VALUE"])))
        except (KeyError, ValueError):
            continue
    rows.sort()
    with (OUT / "ecb_dfr.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "rate"])
        w.writerows(rows)
    print(f"ecb_dfr.csv: {len(rows)} dní, {rows[0][0]} ({rows[0][1]} %) "
          f"→ {rows[-1][0]} ({rows[-1][1]} %)")


def fetch_fed(raw: bytes | None = None) -> None:
    """Fed funds effective rate (DFF), denná."""
    if raw is None:
        raw = _get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF")
    text = raw.decode()
    rows = []
    rd = csv.DictReader(text.splitlines())
    # FRED občas mení názov stĺpca (DFF vs observation_value)
    for r in rd:
        keys = list(r)
        dcol, vcol = keys[0], keys[1]
        try:
            rows.append((r[dcol], float(r[vcol])))
        except ValueError:
            continue
    rows.sort()
    with (OUT / "fed_dff.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "rate"])
        w.writerows(rows)
    print(f"fed_dff.csv: {len(rows)} dní, {rows[0][0]} ({rows[0][1]} %) "
          f"→ {rows[-1][0]} ({rows[-1][1]} %)")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    if "--fed-from-stdin" in sys.argv:
        fetch_fed(sys.stdin.buffer.read())
        sys.exit(0)
    fetch_eurusd()
    fetch_ecb()
    try:
        fetch_fed()
    except Exception as exc:  # noqa: BLE001
        print(f"FRED priamo nedostupný ({exc}).")
        print("Spusti:  ssh hetzner 'curl -s \"https://fred.stlouisfed.org/"
              "graph/fredgraph.csv?id=DFF\"' | python3 fetch_data.py "
              "--fed-from-stdin")
