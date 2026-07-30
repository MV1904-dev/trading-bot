"""Trading subsystem: broker adaptéry, stratégie a pomocné moduly.

IBKRBroker sa načíta až pri prvom použití — eager import ťahal ib_async do
každého importu balíka, hoci cTrader bot IBKR vôbec nepoužíva (IBKR vyradený
29. 7. 2026). Prístup `from trading import IBKRBroker` funguje ďalej.
"""

from typing import Any

__all__ = ["IBKRBroker"]


def __getattr__(name: str) -> Any:
    if name == "IBKRBroker":
        from trading.broker_ibkr import IBKRBroker
        return IBKRBroker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
