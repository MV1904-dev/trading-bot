"""Stiahne M5 históriu z verejných zdrojov (bez účtu / API kľúča) a uloží ju
do data/ v rovnakom formáte, aký číta backtest.py (--offline).

Zdroje
------
* BITCOIN -> Binance verejný dátový archív (data.binance.vision), BTCUSDT 5m,
  mesačné + denné ZIP-y s klines. Hlboká história.
* EURUSD  -> Yahoo Finance chart API, EURUSD=X, 5m (max ~60 dní).
* GOLD    -> Yahoo Finance chart API, GC=F (COMEX gold futures) ako proxy za
  XAUUSD spot, 5m (max ~60 dní).

Meta súbory (data/<symbol>_meta.json) sa NEPREPISUJÚ – ponechávame spread a tick
parametre z XTB, aby model P/L v backteste ostal konzistentný.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import urllib.request
import urllib.error
import zipfile
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PERIOD_MIN = 5

# Od kedy sťahovať BTC (mesačné archívy). Yahoo (FX/zlato) je limitovaný na ~60 dní.
BTC_START_YEAR = 2024
BTC_START_MONTH = 1

UA = {"User-Agent": "Mozilla/5.0 (backtest-data-fetch)"}


def _csv_path(symbol: str) -> str:
    return os.path.join(DATA_DIR, f"{symbol}_M{PERIOD_MIN}.csv")


def _write_candles(symbol: str, rows: list[tuple]) -> None:
    """rows: (ctm_ms, open, high, low, close, vol) – zoradí, deduplikuje, uloží."""
    os.makedirs(DATA_DIR, exist_ok=True)
    dedup: dict[int, tuple] = {}
    for r in rows:
        dedup[int(r[0])] = r
    ordered = [dedup[k] for k in sorted(dedup)]
    with open(_csv_path(symbol), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ctm", "datetime", "open", "high", "low", "close", "vol"])
        for ctm, o, h, l, c, v in ordered:
            dt = datetime.fromtimestamp(ctm / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            w.writerow([ctm, dt, o, h, l, c, v])
    if ordered:
        first = datetime.fromtimestamp(ordered[0][0] / 1000, tz=timezone.utc).date()
        last = datetime.fromtimestamp(ordered[-1][0] / 1000, tz=timezone.utc).date()
        print(f"  {symbol}: {len(ordered)} sviečok ({first} → {last}) "
              f"-> {os.path.relpath(_csv_path(symbol))}")
    else:
        print(f"  {symbol}: 0 sviečok (?)")


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


# --- Binance (BTCUSDT) ------------------------------------------------------

def _norm_ctm(v: int) -> int:
    """Binance mení jednotku času (ms vs µs). Normalizuj na ms."""
    v = int(v)
    return v // 1000 if v > 10 ** 14 else v


def _parse_binance_zip(raw: bytes) -> list[tuple]:
    out: list[tuple] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = z.namelist()[0]
        for line in z.read(name).decode().splitlines():
            parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                ctm = _norm_ctm(int(float(parts[0])))
            except ValueError:
                continue  # hlavičkový riadok
            out.append((ctm, float(parts[1]), float(parts[2]),
                        float(parts[3]), float(parts[4]), float(parts[5])))
    return out


def fetch_bitcoin() -> None:
    print("BITCOIN <- Binance (BTCUSDT 5m) …")
    base = "https://data.binance.vision/data/spot"
    rows: list[tuple] = []
    now = datetime.now(timezone.utc)

    # Mesačné archívy až po posledný ukončený mesiac.
    y, m = BTC_START_YEAR, BTC_START_MONTH
    while (y, m) < (now.year, now.month):
        url = f"{base}/monthly/klines/BTCUSDT/5m/BTCUSDT-5m-{y:04d}-{m:02d}.zip"
        try:
            rows += _parse_binance_zip(_http_get(url))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                print(f"    (mesiac {y}-{m:02d}: HTTP {e.code})")
        m += 1
        if m > 12:
            m, y = 1, y + 1

    # Denné archívy pre prebiehajúci mesiac.
    day = 1
    while True:
        try:
            dt = datetime(now.year, now.month, day, tzinfo=timezone.utc)
        except ValueError:
            break
        if dt > now:
            break
        url = f"{base}/daily/klines/BTCUSDT/5m/BTCUSDT-5m-{dt:%Y-%m-%d}.zip"
        try:
            rows += _parse_binance_zip(_http_get(url))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                print(f"    (deň {dt:%Y-%m-%d}: HTTP {e.code})")
        day += 1

    _write_candles("BITCOIN", rows)


# --- Yahoo Finance (EURUSD, GOLD) -------------------------------------------

def _fetch_yahoo(yahoo_symbol: str) -> list[tuple]:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
           f"?interval=5m&range=60d")
    data = json.loads(_http_get(url))
    res = data["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    o, h, l, c = q["open"], q["high"], q["low"], q["close"]
    v = q.get("volume") or [0] * len(ts)
    rows: list[tuple] = []
    for i, t in enumerate(ts):
        if None in (o[i], h[i], l[i], c[i]):
            continue
        rows.append((int(t) * 1000, float(o[i]), float(h[i]),
                     float(l[i]), float(c[i]), float(v[i] or 0)))
    return rows


def fetch_eurusd() -> None:
    print("EURUSD <- Yahoo Finance (EURUSD=X 5m, ~60d) …")
    _write_candles("EURUSD", _fetch_yahoo("EURUSD=X"))


def fetch_gold() -> None:
    print("GOLD <- Yahoo Finance (GC=F 5m, ~60d; futures proxy za XAUUSD) …")
    _write_candles("GOLD", _fetch_yahoo("GC=F"))


def main() -> int:
    fetch_bitcoin()
    fetch_eurusd()
    fetch_gold()
    print("Hotovo. Spusti: python3 backtest.py --offline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
