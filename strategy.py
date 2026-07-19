"""Obchodná stratégia – znovupoužiteľný modul.

Tento modul obsahuje čistú logiku stratégie (bez xAPI a bez závislostí navyše),
takže ho vie použiť backtest.py aj neskorší živý bot. Pracuje nad postupnosťou
sviečok reprezentovaných ako `Candle`.

Stratégia
---------
* Signál: prerazenie maxima/minima posledných `breakout_lookback` sviečok.
* Filter trendu: long len ak cena > EMA(`ema_period`), short len ak cena < EMA.
* Stop loss: `atr_sl_mult` × ATR(`atr_period`); take profit: `tp_rr` × SL.
* Obchodné hodiny: `session` (napr. 9–21). Symboly v `always_open`
  (napr. BITCOIN) obchodujú bez časového obmedzenia.
* Max. 1 pozícia na symbol – toto obmedzenie vynucuje volajúci (backtest/bot),
  modul len generuje signál na danej sviečke.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, NamedTuple, Optional, Sequence


class Candle(NamedTuple):
    ctm: int          # čas začiatku sviečky v ms (epoch)
    open: float
    high: float
    low: float
    close: float
    vol: float


class Signal(NamedTuple):
    side: str         # "long" alebo "short"
    entry: float      # navrhovaná vstupná cena (close signálnej sviečky)
    sl: float         # stop loss (cena)
    tp: float         # take profit (cena)
    atr: float        # hodnota ATR pri vstupe (informatívne)


@dataclass
class StrategyConfig:
    breakout_lookback: int = 20
    ema_period: int = 50
    atr_period: int = 14
    atr_sl_mult: float = 1.5
    tp_rr: float = 2.0                       # TP = tp_rr × SL
    session: tuple = (9, 21)                 # obchodné hodiny [od, do)
    always_open: frozenset = field(default_factory=lambda: frozenset({"BITCOIN"}))


class Indicators(NamedTuple):
    ema: List[Optional[float]]
    atr: List[Optional[float]]
    prior_high: List[Optional[float]]        # max high posledných N sviečok PRED i
    prior_low: List[Optional[float]]         # min low posledných N sviečok PRED i


# --- Indikátory -------------------------------------------------------------

def ema(values: Sequence[float], period: int) -> List[Optional[float]]:
    """Exponenciálny kĺzavý priemer; None kým nie je dosť dát (seed = SMA)."""
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def atr(candles: Sequence[Candle], period: int) -> List[Optional[float]]:
    """Average True Range (Wilderovo vyhladenie); None kým nie je dosť dát."""
    n = len(candles)
    out: List[Optional[float]] = [None] * n
    if n <= period:
        return out
    trs: List[float] = [candles[0].high - candles[0].low]
    for i in range(1, n):
        h, l, pc = candles[i].high, candles[i].low, candles[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    # seed = jednoduchý priemer prvých `period` TR, index period-1
    seed = sum(trs[1:period + 1]) / period if n > period else None
    if seed is None:
        return out
    out[period] = seed
    prev = seed
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def _rolling_extreme(values: Sequence[float], window: int, want_max: bool) -> List[Optional[float]]:
    """Max/min z `window` hodnôt PRED indexom i (i sama sa nezapočítava)."""
    out: List[Optional[float]] = [None] * len(values)
    for i in range(window, len(values)):
        chunk = values[i - window:i]
        out[i] = max(chunk) if want_max else min(chunk)
    return out


def compute_indicators(candles: Sequence[Candle], config: StrategyConfig) -> Indicators:
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    return Indicators(
        ema=ema(closes, config.ema_period),
        atr=atr(candles, config.atr_period),
        prior_high=_rolling_extreme(highs, config.breakout_lookback, True),
        prior_low=_rolling_extreme(lows, config.breakout_lookback, False),
    )


# --- Stratégia --------------------------------------------------------------

class Strategy:
    """Zapuzdruje pravidlá vstupu. Rovnaká inštancia sa dá použiť v backteste
    aj v živom bote (ten si drží posuvné okno sviečok a volá `signal_at`)."""

    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config = config or StrategyConfig()

    def in_session(self, symbol: str, ctm_ms: int) -> bool:
        """Je daný čas v povolených obchodných hodinách pre symbol?"""
        if symbol in self.config.always_open:
            return True
        from datetime import datetime, timezone
        hour = datetime.fromtimestamp(ctm_ms / 1000, tz=timezone.utc).hour
        start, end = self.config.session
        return start <= hour < end

    def signal_at(
        self,
        symbol: str,
        index: int,
        candles: Sequence[Candle],
        ind: Indicators,
    ) -> Optional[Signal]:
        """Vráti Signal, ak na sviečke `index` vznikol vstup, inak None."""
        e = ind.ema[index]
        a = ind.atr[index]
        ph = ind.prior_high[index]
        pl = ind.prior_low[index]
        if None in (e, a, ph, pl):
            return None

        c = candles[index]
        if not self.in_session(symbol, c.ctm):
            return None

        cfg = self.config
        sl_dist = cfg.atr_sl_mult * a
        tp_dist = cfg.tp_rr * sl_dist

        # long: prerazenie maxima + cena nad EMA
        if c.close > ph and c.close > e:
            return Signal("long", c.close, c.close - sl_dist, c.close + tp_dist, a)
        # short: prerazenie minima + cena pod EMA
        if c.close < pl and c.close < e:
            return Signal("short", c.close, c.close + sl_dist, c.close - tp_dist, a)
        return None

    def latest_signal(self, symbol: str, candles: Sequence[Candle]) -> Optional[Signal]:
        """Pohodlný vstup pre živý bot – vyhodnotí poslednú sviečku."""
        if not candles:
            return None
        ind = compute_indicators(candles, self.config)
        return self.signal_at(symbol, len(candles) - 1, candles, ind)
