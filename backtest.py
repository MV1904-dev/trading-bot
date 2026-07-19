"""Backtest breakout stratégie na dátach z XTB (EURUSD, GOLD, BITCOIN).

Použitie
--------
    python3 backtest.py            # použije cache v data/, ak chýba, stiahne
    python3 backtest.py --refresh  # vynúti opätovné stiahnutie histórie
    python3 backtest.py --offline  # nič nesťahuje, beží len z cache

Priebeh
-------
1. Stiahne (a nacachuje do data/) maximum dostupnej histórie 5-min sviečok pre
   každý symbol cez getChartRangeRequest. Spread a parametre kontraktu berie z
   getSymbol a ukladá do <symbol>_meta.json.
2. Spustí event-driven simuláciu na jednom spoločnom účte (10 000 EUR),
   risk 1 % na obchod, max 1 pozícia na symbol, so zohľadnením spreadu.
3. Vypíše štatistiky za každý symbol aj spolu a uloží zoznam obchodov do CSV.

Model P/L (zjednodušenie, jasne zdokumentované)
-----------------------------------------------
* Cena sviečky = mid. Vstup na close signálnej sviečky.
* Spread sa účtuje ako jednorazový náklad na obchod (≈ jedno prekríženie bid/ask):
  spread_cost = spread × (tickValue / tickSize) × objem.
* Hodnota pohybu ceny: (tickValue / tickSize) za 1 lot; považujeme ju za hodnotu
  v mene účtu (pri EUR demo účte dostatočne presné pre sanity backtest).
* Veľkosť pozície: objem tak, aby strata na SL ≈ 1 % aktuálneho kapitálu
  (zaokrúhlené na lotStep, minimálne lotMin).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from strategy import Candle, Indicators, Strategy, StrategyConfig, compute_indicators

# --- Konfigurácia -----------------------------------------------------------
SYMBOLS = ["EURUSD", "GOLD", "BITCOIN"]
PERIOD_MIN = 5
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HISTORY_YEARS = 10                 # ako ďaleko dozadu žiadame (XTB vráti čo má)
STARTING_CAPITAL = 10_000.0
RISK_PCT = 0.01

XTB_DEMO_URL = "wss://ws.xtb.com/demo"
CONNECT_TIMEOUT = 20
API_PAUSE = 0.3


# --- Sťahovanie a cache -----------------------------------------------------

def _candles_from_chart(return_data: dict) -> List[Candle]:
    """Prevedie odpoveď getChartRangeRequest na reálne ceny.

    XTB kóduje ceny ako celé čísla; reálna cena = hodnota / 10^digits.
    Polia high/low/close sú posun voči open.
    """
    digits = return_data.get("digits", 0)
    factor = 10 ** digits
    out: List[Candle] = []
    for r in return_data.get("rateInfos", []):
        o = r["open"] / factor
        out.append(Candle(
            ctm=int(r["ctm"]),
            open=o,
            high=(r["open"] + r["high"]) / factor,
            low=(r["open"] + r["low"]) / factor,
            close=(r["open"] + r["close"]) / factor,
            vol=float(r.get("vol", 0.0)),
        ))
    out.sort(key=lambda c: c.ctm)
    return out


def _csv_path(symbol: str) -> str:
    return os.path.join(DATA_DIR, f"{symbol}_M{PERIOD_MIN}.csv")


def _meta_path(symbol: str) -> str:
    return os.path.join(DATA_DIR, f"{symbol}_meta.json")


def _save_cache(symbol: str, candles: List[Candle], meta: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(_csv_path(symbol), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ctm", "datetime", "open", "high", "low", "close", "vol"])
        for c in candles:
            dt = datetime.fromtimestamp(c.ctm / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            w.writerow([c.ctm, dt, c.open, c.high, c.low, c.close, c.vol])
    with open(_meta_path(symbol), "w") as f:
        json.dump(meta, f, indent=2)


def _load_cache(symbol: str) -> Optional[tuple]:
    cp, mp = _csv_path(symbol), _meta_path(symbol)
    if not (os.path.exists(cp) and os.path.exists(mp)):
        return None
    candles: List[Candle] = []
    with open(cp, newline="") as f:
        for row in csv.DictReader(f):
            candles.append(Candle(
                ctm=int(row["ctm"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                vol=float(row["vol"]),
            ))
    with open(mp) as f:
        meta = json.load(f)
    return candles, meta


def fetch_symbol(ws, symbol: str) -> tuple:
    """Stiahne meta (getSymbol) a históriu (getChartRangeRequest) pre symbol."""
    from test_connection import send_command  # znovupoužijeme jednoduchý helper

    def call(command, arguments=None):
        resp = send_command(ws, command, arguments)
        time.sleep(API_PAUSE)
        if not resp.get("status"):
            raise RuntimeError(f"{command} zlyhal: kód {resp.get('errorCode')} "
                               f"{resp.get('errorDescr')}")
        return resp.get("returnData")

    info = call("getSymbol", {"symbol": symbol})
    meta = {
        "symbol": symbol,
        "digits": info.get("precision", 5),
        "spread": round(info["ask"] - info["bid"], 8),
        "lotMin": info.get("lotMin", 0.01),
        "lotStep": info.get("lotStep", 0.01),
        "contractSize": info.get("contractSize", 1),
        "tickSize": info.get("tickSize") or (10 ** -info.get("precision", 5)),
        "tickValue": info.get("tickValue", 1.0),
        "currency": info.get("currency", ""),
        "currencyProfit": info.get("currencyProfit", ""),
    }

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - HISTORY_YEARS * 365 * 24 * 3600 * 1000
    chart = call("getChartRangeRequest", {"info": {
        "period": PERIOD_MIN, "start": start_ms, "end": now_ms,
        "symbol": symbol, "ticks": 0,
    }})
    candles = _candles_from_chart(chart)
    return candles, meta


def download_all(refresh: bool) -> None:
    """Doplní chýbajúce (alebo všetky pri --refresh) symboly do cache."""
    from dotenv import load_dotenv
    from websocket import create_connection

    missing = [s for s in SYMBOLS if refresh or _load_cache(s) is None]
    if not missing:
        return

    load_dotenv()
    user_id = os.getenv("XTB_USER_ID")
    password = os.getenv("XTB_PASSWORD")
    if not user_id or not password:
        raise SystemExit("CHYBA: V .env chýba XTB_USER_ID / XTB_PASSWORD "
                         "(potrebné na stiahnutie histórie).")

    print(f"Sťahujem históriu pre: {', '.join(missing)} …")
    ws = create_connection(XTB_DEMO_URL, timeout=CONNECT_TIMEOUT)
    try:
        from test_connection import send_command
        login = send_command(ws, "login", {"userId": user_id, "password": password})
        if not login.get("status"):
            raise SystemExit(f"CHYBA: Prihlásenie zlyhalo: {login.get('errorDescr')}")
        for symbol in missing:
            candles, meta = fetch_symbol(ws, symbol)
            _save_cache(symbol, candles, meta)
            print(f"  {symbol}: {len(candles)} sviečok uložených do "
                  f"{os.path.relpath(_csv_path(symbol))}")
    finally:
        try:
            send_command(ws, "logout")
        finally:
            ws.close()


# --- Backtest engine --------------------------------------------------------

@dataclass
class Position:
    symbol: str
    side: str
    entry: float
    sl: float
    tp: float
    volume: float
    entry_ctm: int
    money_per_price: float     # (tickValue / tickSize) × volume


@dataclass
class Trade:
    symbol: str
    side: str
    entry_ctm: int
    exit_ctm: int
    entry: float
    exit: float
    volume: float
    reason: str                # "TP" / "SL"
    pnl: float                 # čistý P/L vrátane spreadu (v mene účtu)


def _money_per_price_unit(meta: dict) -> float:
    """Hodnota pohybu ceny o 1.0 na 1 lot (mena účtu)."""
    tick_size = meta.get("tickSize") or 1.0
    return meta.get("tickValue", 1.0) / tick_size


def _position_volume(equity: float, sl_dist: float, meta: dict) -> float:
    """Objem tak, aby strata na SL ≈ RISK_PCT × equity."""
    per_price = _money_per_price_unit(meta)
    loss_per_lot = sl_dist * per_price
    if loss_per_lot <= 0:
        return meta.get("lotMin", 0.01)
    raw = (RISK_PCT * equity) / loss_per_lot
    step = meta.get("lotStep", 0.01) or 0.01
    vol = math.floor(raw / step) * step
    return round(max(vol, meta.get("lotMin", 0.01)), 8)


def _check_exit(pos: Position, c: Candle) -> Optional[tuple]:
    """Vráti (exit_price, reason) ak sviečka zasiahla SL/TP. Konzervatívne:
    ak sviečka pretne oboje, počítame SL (horší scenár)."""
    if pos.side == "long":
        if c.low <= pos.sl:
            return pos.sl, "SL"
        if c.high >= pos.tp:
            return pos.tp, "TP"
    else:  # short
        if c.high >= pos.sl:
            return pos.sl, "SL"
        if c.low <= pos.tp:
            return pos.tp, "TP"
    return None


def run_backtest(
    data: Dict[str, List[Candle]],
    metas: Dict[str, dict],
    config: Optional[StrategyConfig] = None,
) -> List[Trade]:
    """Event-driven simulácia na spoločnom účte v chronologickom poradí."""
    strat = Strategy(config)
    indicators: Dict[str, Indicators] = {
        s: compute_indicators(data[s], strat.config) for s in data
    }
    index_of: Dict[str, Dict[int, int]] = {
        s: {c.ctm: i for i, c in enumerate(data[s])} for s in data
    }

    # zjednotená časová os
    all_ctms = sorted({c.ctm for s in data for c in data[s]})

    equity = STARTING_CAPITAL
    positions: Dict[str, Position] = {}
    trades: List[Trade] = []

    for ctm in all_ctms:
        for symbol in data:
            i = index_of[symbol].get(ctm)
            if i is None:
                continue
            candle = data[symbol][i]

            # 1) správa otvorenej pozície
            pos = positions.get(symbol)
            if pos is not None:
                ex = _check_exit(pos, candle)
                if ex is not None:
                    exit_price, reason = ex
                    if pos.side == "long":
                        gross = (exit_price - pos.entry) * pos.money_per_price
                    else:
                        gross = (pos.entry - exit_price) * pos.money_per_price
                    spread_cost = metas[symbol]["spread"] * pos.money_per_price
                    pnl = gross - spread_cost
                    equity += pnl
                    trades.append(Trade(
                        symbol=symbol, side=pos.side, entry_ctm=pos.entry_ctm,
                        exit_ctm=candle.ctm, entry=pos.entry, exit=exit_price,
                        volume=pos.volume, reason=reason, pnl=pnl,
                    ))
                    del positions[symbol]
                    pos = None

            # 2) vstup, ak sme flat (max 1 pozícia na symbol)
            if symbol not in positions:
                sig = strat.signal_at(symbol, i, data[symbol], indicators[symbol])
                if sig is not None:
                    sl_dist = abs(sig.entry - sig.sl)
                    vol = _position_volume(equity, sl_dist, metas[symbol])
                    positions[symbol] = Position(
                        symbol=symbol, side=sig.side, entry=sig.entry,
                        sl=sig.sl, tp=sig.tp, volume=vol, entry_ctm=candle.ctm,
                        money_per_price=_money_per_price_unit(metas[symbol]) * vol,
                    )

    return trades


# --- Štatistiky a výstup ----------------------------------------------------

def _max_drawdown(equity_curve: List[float]) -> float:
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    return max_dd


def _stats(trades: List[Trade], starting: float) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "pnl": 0.0,
                "max_dd": 0.0, "profit_factor": None}
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    equity_curve = [starting]
    eq = starting
    for t in sorted(trades, key=lambda x: x.exit_ctm):
        eq += t.pnl
        equity_curve.append(eq)
    return {
        "trades": n,
        "win_rate": len(wins) / n,
        "pnl": sum(t.pnl for t in trades),
        "max_dd": _max_drawdown(equity_curve),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else math.inf,
    }


def _fmt_pf(pf) -> str:
    if pf is None:
        return "—"
    if pf == math.inf:
        return "∞ (bez strát)"
    return f"{pf:.2f}"


def print_report(trades: List[Trade], metas: Dict[str, dict]) -> None:
    print("\n" + "=" * 64)
    print(f"VÝSLEDKY BACKTESTU  (kapitál {STARTING_CAPITAL:,.0f} EUR, "
          f"risk {RISK_PCT:.0%}/obchod)")
    print("=" * 64)
    header = f"{'Symbol':<10}{'Obch.':>7}{'Win%':>8}{'P/L':>13}{'MaxDD':>12}{'PF':>16}"
    print(header)
    print("-" * 64)
    for symbol in SYMBOLS:
        sym_trades = [t for t in trades if t.symbol == symbol]
        st = _stats(sym_trades, STARTING_CAPITAL)
        cur = metas.get(symbol, {}).get("currencyProfit", "")
        print(f"{symbol:<10}{st['trades']:>7}{st['win_rate']*100:>7.1f}%"
              f"{st['pnl']:>12.2f}{st['max_dd']:>12.2f}{_fmt_pf(st['profit_factor']):>16}")
    print("-" * 64)
    total = _stats(trades, STARTING_CAPITAL)
    print(f"{'SPOLU':<10}{total['trades']:>7}{total['win_rate']*100:>7.1f}%"
          f"{total['pnl']:>12.2f}{total['max_dd']:>12.2f}{_fmt_pf(total['profit_factor']):>16}")
    print("=" * 64)
    print(f"Konečný kapitál: {STARTING_CAPITAL + total['pnl']:,.2f} EUR")


def save_trades_csv(trades: List[Trade], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "side", "entry_time", "exit_time",
                    "entry", "exit", "volume", "reason", "pnl"])
        for t in sorted(trades, key=lambda x: x.exit_ctm):
            et = datetime.fromtimestamp(t.entry_ctm / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            xt = datetime.fromtimestamp(t.exit_ctm / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            w.writerow([t.symbol, t.side, et, xt, t.entry, t.exit,
                        t.volume, t.reason, round(t.pnl, 2)])
    print(f"Zoznam {len(trades)} obchodov uložený do {os.path.relpath(path)}")


# --- Main -------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest breakout stratégie na XTB dátach.")
    parser.add_argument("--refresh", action="store_true", help="vynúti opätovné stiahnutie histórie")
    parser.add_argument("--offline", action="store_true", help="nesťahuj nič, použi len cache")
    args = parser.parse_args()

    if not args.offline:
        try:
            download_all(refresh=args.refresh)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"UPOZORNENIE: Sťahovanie zlyhalo ({exc}). Skúšam bežať z cache…")

    data: Dict[str, List[Candle]] = {}
    metas: Dict[str, dict] = {}
    for symbol in SYMBOLS:
        cached = _load_cache(symbol)
        if cached is None:
            print(f"CHYBA: Chýbajú dáta pre {symbol} v {os.path.relpath(DATA_DIR)} "
                  f"(spusti bez --offline na stiahnutie).")
            return 1
        candles, meta = cached
        data[symbol] = candles
        metas[symbol] = meta
        print(f"{symbol}: {len(candles)} sviečok "
              f"({datetime.fromtimestamp(candles[0].ctm/1000, tz=timezone.utc):%Y-%m-%d} → "
              f"{datetime.fromtimestamp(candles[-1].ctm/1000, tz=timezone.utc):%Y-%m-%d}), "
              f"spread {meta['spread']}")

    trades = run_backtest(data, metas)
    print_report(trades, metas)
    save_trades_csv(trades, os.path.join(DATA_DIR, "backtest_trades.csv"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
