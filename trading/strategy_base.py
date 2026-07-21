"""Spoločné rozhranie stratégií bota.

Každá stratégia je modul s vlastným ID, konfiguráciou a on/off prepínačom.
Bot volá on_bar() po uzavretí každého baru; stratégia vracia signály,
o exekúcii rozhoduje spoločná exekučná + risk vrstva v bot.py. Každý príkaz
nesie orderRef s ID stratégie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Bar:
    ts: float          # epoch začiatku baru (UTC)
    open: float
    high: float
    low: float
    close: float


@dataclass
class Signal:
    strategy_id: str
    side: str                       # "long" | "short"
    qty: float
    tp_price: float                 # limitka TP; SL sa nepoužíva
    reason: str = ""
    context: dict = field(default_factory=dict)


class StrategyBase:
    """Základ pre všetky stratégie. Podtrieda definuje ID a on_bar()."""

    id: str = "BASE"
    enabled: bool = False

    def on_bar(self, bar: Bar, atr: Optional[float]) -> list[Signal]:
        """Zavolané po uzavretí baru. Vracia signály na otvorenie pozícií."""
        return []

    def on_trade_opened(self, trade_id: int, side: str, price: float) -> None:
        """Exekučná vrstva potvrdila otvorenie (fill)."""

    def on_trade_closed(self, trade_id: int, side: str, price: float) -> None:
        """TP naplnený — pozícia zavretá."""

    def restore(self, open_trades: list) -> None:
        """Obnova interného stavu z DB riadkov (status='open') po reštarte."""

    def status_line(self) -> str:
        return f"{self.id}: {'ON' if self.enabled else 'OFF'}"
