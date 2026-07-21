"""Približné sadzby Fed/ECB pre výpočet overnight fundingu EURUSD.

Rovnaká tabuľka ako v backtest_v2.py (mesačná granularita, oficiálne sadzby:
Fed horná hranica pásma, ECB depozitná). Long EURUSD platí (Fed − ECB + 1 %)
p.a., short inkasuje max(Fed − ECB − 1 %, 0) p.a. — 1 % je broker prirážka.
"""

from __future__ import annotations

import bisect

FUNDING_MARKUP = 1.0  # % p.a.

FED_RATES = [
    ("2013-01-01", 0.25), ("2015-12-17", 0.50), ("2016-12-15", 0.75),
    ("2017-03-16", 1.00), ("2017-06-15", 1.25), ("2017-12-14", 1.50),
    ("2018-03-22", 1.75), ("2018-06-14", 2.00), ("2018-09-27", 2.25),
    ("2018-12-20", 2.50), ("2019-08-01", 2.25), ("2019-09-19", 2.00),
    ("2019-10-31", 1.75), ("2020-03-04", 1.25), ("2020-03-16", 0.25),
    ("2022-03-17", 0.50), ("2022-05-05", 1.00), ("2022-06-16", 1.75),
    ("2022-07-28", 2.50), ("2022-09-22", 3.25), ("2022-11-03", 4.00),
    ("2022-12-15", 4.50), ("2023-02-02", 4.75), ("2023-03-23", 5.00),
    ("2023-05-04", 5.25), ("2023-07-27", 5.50), ("2024-09-19", 5.00),
    ("2024-11-08", 4.75), ("2024-12-19", 4.50), ("2025-09-18", 4.25),
    ("2025-10-30", 4.00), ("2025-12-11", 3.75),
]
ECB_RATES = [
    ("2013-01-01", 0.00), ("2014-06-11", -0.10), ("2014-09-10", -0.20),
    ("2015-12-09", -0.30), ("2016-03-16", -0.40), ("2019-09-18", -0.50),
    ("2022-07-27", 0.00), ("2022-09-14", 0.75), ("2022-11-02", 1.50),
    ("2022-12-21", 2.00), ("2023-02-08", 2.50), ("2023-03-22", 3.00),
    ("2023-05-10", 3.25), ("2023-06-21", 3.50), ("2023-08-02", 3.75),
    ("2023-09-20", 4.00), ("2024-06-12", 3.75), ("2024-09-18", 3.50),
    ("2024-10-23", 3.25), ("2024-12-18", 3.00), ("2025-02-05", 2.75),
    ("2025-03-12", 2.50), ("2025-04-23", 2.25), ("2025-06-11", 2.00),
]

_FED_DATES = [d for d, _ in FED_RATES]
_ECB_DATES = [d for d, _ in ECB_RATES]


def rate_diff(day: str) -> float:
    """Fed − ECB v % p.a. k dátumu 'YYYY-MM-DD'."""
    fed = FED_RATES[max(bisect.bisect_right(_FED_DATES, day) - 1, 0)][1]
    ecb = ECB_RATES[max(bisect.bisect_right(_ECB_DATES, day) - 1, 0)][1]
    return fed - ecb


def funding_rates_pa(day: str) -> tuple[float, float]:
    """(long_platí, short_inkasuje) v % p.a. k danému dňu."""
    diff = rate_diff(day)
    return max(diff + FUNDING_MARKUP, 0.0), max(diff - FUNDING_MARKUP, 0.0)


def daily_funding_usd(day: str, side: str, qty: float, price: float) -> float:
    """Denný funding v USD pre pozíciu (záporné = platíš)."""
    long_pa, short_pa = funding_rates_pa(day)
    notional = qty * price
    if side == "long":
        return -long_pa / 100.0 / 365.0 * notional
    return short_pa / 100.0 / 365.0 * notional
