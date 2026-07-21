"""Grid25 — pásmový grid EURUSD, parametre víťaza z backtest_v2.

* pozícia 25 000 jednotiek EUR (objem na IDEALPRO sa zadáva v základnej
  mene páru; „25k“ zo zadania)
* short vstup pri raste +0.15 % od referenčného minima / poslednej úrovne
* long vstup pri poklese −0.225 % (1.5×) od referenčného maxima, navyše
  len ak pokles > 2× ATR(14, M5)
* TP +0.1 % vo svoj prospech, žiadny SL
* pásma: pod 1.1200 len long, nad 1.1600 len short, medzi obojsmerne
* kapacita 20 + 10 rezervných úrovní na smer; rezervné len pri cene
  > 2× ATR od poslednej úrovne daného smeru
* max 1 vstup na smer a bar
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from trading.strategy_base import Bar, Signal, StrategyBase


@dataclass
class Grid25Config:
    pair: str = "EURUSD"
    qty: float = 25_000            # jednotky EUR
    step_short: float = 0.0015     # +0.15 %
    step_long: float = 0.00225     # −0.225 % (1.5× short)
    tp_pct: float = 0.001          # +0.1 %
    band_low: float = 1.1200
    band_high: float = 1.1600
    atr_mult: float = 2.0
    base_levels: int = 20
    reserve_levels: int = 10

    @property
    def cap(self) -> int:
        return self.base_levels + self.reserve_levels


class Grid25(StrategyBase):
    id = "GRID25"
    enabled = True

    def __init__(self, config: Optional[Grid25Config] = None):
        self.cfg = config or Grid25Config()
        self.longs: dict[int, float] = {}    # trade_id -> entry
        self.shorts: dict[int, float] = {}
        self.ref_long: Optional[float] = None
        self.ref_short: Optional[float] = None
        self.last_long = 0.0
        self.last_short = 0.0

    # --- obnova po reštarte ------------------------------------------------
    def restore(self, open_trades: list) -> None:
        for t in open_trades:
            if t["side"] == "long":
                self.longs[t["id"]] = t["entry_price"]
            else:
                self.shorts[t["id"]] = t["entry_price"]
            ctx = json.loads(t["context"] or "{}")
            self.last_long = ctx.get("last_long", self.last_long)
            self.last_short = ctx.get("last_short", self.last_short)
        if self.longs:
            self.last_long = self.last_long or max(self.longs.values())
        if self.shorts:
            self.last_short = self.last_short or min(self.shorts.values())

    # --- jadro -------------------------------------------------------------
    def on_bar(self, bar: Bar, atr: Optional[float]) -> list[Signal]:
        c = bar.close
        cfg = self.cfg

        # inicializácia / update referenčných extrémov
        self.ref_long = max(self.ref_long or c, bar.high)
        self.ref_short = min(self.ref_short or c, bar.low)

        if atr is None:
            return []

        signals: list[Signal] = []
        allow_long = c < cfg.band_high
        allow_short = c > cfg.band_low

        if allow_long and len(self.longs) < cfg.cap:
            drop = self.ref_long - c
            trigger = max(self.ref_long * cfg.step_long, cfg.atr_mult * atr)
            unlock = (len(self.longs) < cfg.base_levels
                      or abs(c - self.last_long) > cfg.atr_mult * atr)
            if drop >= trigger and unlock:
                signals.append(Signal(
                    strategy_id=self.id, side="long", qty=cfg.qty,
                    tp_price=round(c * (1 + cfg.tp_pct), 5),
                    reason=f"pokles {drop:.5f} ≥ max(krok, 2×ATR) "
                           f"od ref {self.ref_long:.5f}",
                    context={"ref_long": self.ref_long, "atr": atr,
                             "levels": len(self.longs),
                             "last_long": self.last_long},
                ))

        if allow_short and len(self.shorts) < cfg.cap:
            rise = c - self.ref_short
            unlock = (len(self.shorts) < cfg.base_levels
                      or abs(c - self.last_short) > cfg.atr_mult * atr)
            if rise >= self.ref_short * cfg.step_short and unlock:
                signals.append(Signal(
                    strategy_id=self.id, side="short", qty=cfg.qty,
                    tp_price=round(c * (1 - cfg.tp_pct), 5),
                    reason=f"rast {rise:.5f} ≥ krok od ref {self.ref_short:.5f}",
                    context={"ref_short": self.ref_short, "atr": atr,
                             "levels": len(self.shorts),
                             "last_short": self.last_short},
                ))
        return signals

    def on_trade_opened(self, trade_id: int, side: str, price: float) -> None:
        if side == "long":
            self.longs[trade_id] = price
            self.last_long = price
            self.ref_long = price       # nová kotva po vstupe
        else:
            self.shorts[trade_id] = price
            self.last_short = price
            self.ref_short = price

    def on_trade_closed(self, trade_id: int, side: str, price: float) -> None:
        if side == "long":
            self.longs.pop(trade_id, None)
            if not self.longs:
                self.ref_long = price   # reset kotvy, keď je strana flat
        else:
            self.shorts.pop(trade_id, None)
            if not self.shorts:
                self.ref_short = price

    def status_line(self) -> str:
        return (f"{self.id}: {'ON' if self.enabled else 'OFF'} | "
                f"long {len(self.longs)}/{self.cfg.cap}, "
                f"short {len(self.shorts)}/{self.cfg.cap} | "
                f"ref_L {self.ref_long or 0:.5f} ref_S {self.ref_short or 0:.5f}")
