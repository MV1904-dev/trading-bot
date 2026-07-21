"""IBKR broker adapter built on ib_async.

A thin, opinionated wrapper around Interactive Brokers (TWS / IB Gateway)
designed to be shared by both the live trading bot and the backtester.

Design goals
------------
* One class, ``IBKRBroker``, exposing everything the strategy layer needs:
  connection, account state, live quotes, historical bars, order
  placement / cancellation and open positions.
* Historical data is cached to CSV under ``data/`` and updated
  incrementally, so a backtest can run fully offline via
  :meth:`IBKRBroker.load_cached` without ever touching the network.
* Sensible defaults for an IB Gateway **paper** account:
  ``127.0.0.1:4002`` (Gateway paper port), localhost only.

Live usage (bot)::

    from trading.broker_ibkr import IBKRBroker

    with IBKRBroker(port=4002) as ib:
        print(ib.account_summary())
        print(ib.quote(ib.forex("EURUSD")))
        df = ib.history_cached(ib.forex("EURUSD"), bar_size="5 mins")

Offline usage (backtest)::

    from trading.broker_ibkr import IBKRBroker
    df = IBKRBroker.load_cached("EURUSD", "5 mins")  # reads data/EURUSD_M5.csv

Nothing here is TWS-version specific; the same code targets TWS (7497/7496)
or IB Gateway (4002 paper / 4001 live) — just pass the right ``port``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Union

from ib_async import (
    IB,
    Contract,
    Forex,
    LimitOrder,
    MarketOrder,
    Stock,
    StopOrder,
    Ticker,
    util,
)

log = logging.getLogger(__name__)

# Default IB Gateway paper-trading endpoint (see docs/ibkr-setup.md).
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4002  # IB Gateway paper. Live Gateway = 4001, TWS = 7497/7496.
DEFAULT_CLIENT_ID = 17

# Where cached CSV history lives. Repo-root ``data/`` by default; overridable.
DEFAULT_DATA_DIR = Path(os.environ.get("IBKR_DATA_DIR", "data"))

# Prefix for IBKR cache files. ``data/`` is shared with the XTB/alt-source
# backtest cache, which uses the same ``<SYMBOL>_M5.csv`` name but a different
# schema (ctm,datetime,... vs date,open,...). Without this prefix the two
# overwrite each other.
CACHE_PREFIX = "ibkr_"

# Map a human "bar size" string to the short label used in cache filenames.
_BAR_LABELS = {
    "1 secs": "S1", "5 secs": "S5", "10 secs": "S10", "15 secs": "S15",
    "30 secs": "S30",
    "1 min": "M1", "2 mins": "M2", "3 mins": "M3", "5 mins": "M5",
    "10 mins": "M10", "15 mins": "M15", "20 mins": "M20", "30 mins": "M30",
    "1 hour": "H1", "2 hours": "H2", "3 hours": "H3", "4 hours": "H4",
    "8 hours": "H8",
    "1 day": "D1", "1 week": "W1", "1 month": "MN1",
}

# Per-request chunk size when back-filling deep history. IBKR caps the
# duration of a single ``reqHistoricalData`` call by bar size; these values
# stay comfortably inside the documented limits so requests don't get
# rejected, and we simply loop backwards until IBKR runs out of data.
_CHUNK_BY_BAR = {
    "S1": "1800 S", "S5": "3600 S", "S10": "14400 S", "S15": "14400 S",
    "S30": "28800 S",
    "M1": "1 D", "M2": "2 D", "M3": "5 D", "M5": "1 W",
    "M10": "2 W", "M15": "2 W", "M20": "1 M", "M30": "1 M",
    "H1": "1 M", "H2": "2 M", "H3": "3 M", "H4": "6 M", "H8": "1 Y",
    "D1": "10 Y", "W1": "20 Y", "MN1": "30 Y",
}


def _bar_label(bar_size: str) -> str:
    """``"5 mins"`` -> ``"M5"`` (used in cache filenames)."""
    try:
        return _BAR_LABELS[bar_size]
    except KeyError:
        return bar_size.replace(" ", "_")


def _as_utc(dt: datetime) -> datetime:
    """Normalise a datetime to tz-aware UTC for consistent comparisons."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class IBKRBroker:
    """Shared IBKR adapter for the bot and the backtester.

    The instance is safe to use as a context manager; it connects on
    ``__enter__`` and disconnects on ``__exit__``.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        client_id: int = DEFAULT_CLIENT_ID,
        *,
        readonly: bool = False,
        account: str = "",
        data_dir: Union[str, Path] = DEFAULT_DATA_DIR,
        market_data_type: int = 1,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.readonly = readonly
        self.account = account
        self.market_data_type = market_data_type
        self.data_dir = Path(data_dir)
        self.ib = IB()

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    def connect(self, timeout: float = 8.0) -> "IBKRBroker":
        """Connect to a running TWS / IB Gateway instance."""
        if self.ib.isConnected():
            return self
        log.info("Connecting to IBKR at %s:%s (clientId=%s, readonly=%s)",
                 self.host, self.port, self.client_id, self.readonly)
        self.ib.connect(
            self.host, self.port, clientId=self.client_id,
            readonly=self.readonly, account=self.account, timeout=timeout,
        )
        # 1=real-time, 2=frozen, 3=delayed, 4=delayed-frozen. We fall back to
        # delayed automatically if a real-time subscription is missing.
        try:
            self.ib.reqMarketDataType(self.market_data_type)
        except Exception:  # pragma: no cover - non-fatal
            log.debug("reqMarketDataType(%s) failed", self.market_data_type)
        managed = ", ".join(self.ib.managedAccounts()) or "?"
        log.info("Connected. Managed accounts: %s", managed)
        return self

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()
            log.info("Disconnected from IBKR.")

    @property
    def connected(self) -> bool:
        return self.ib.isConnected()

    def __enter__(self) -> "IBKRBroker":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.disconnect()

    def _require_connection(self) -> None:
        if not self.ib.isConnected():
            raise ConnectionError(
                "Not connected to IBKR. Start IB Gateway (paper, API on port "
                f"{self.port}) and call .connect() first."
            )

    # ------------------------------------------------------------------ #
    # Contracts
    # ------------------------------------------------------------------ #
    def qualify(self, contract: Contract) -> Contract:
        """Resolve an under-specified contract against IBKR (conId, exchange…)."""
        self._require_connection()
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(f"Could not qualify contract: {contract}")
        return qualified[0]

    def forex(self, pair: str = "EURUSD") -> Contract:
        """A qualified FX contract, e.g. ``forex("EURUSD")`` (IDEALPRO)."""
        return self.qualify(Forex(pair))

    def stock(self, symbol: str, exchange: str = "SMART",
              currency: str = "USD") -> Contract:
        return self.qualify(Stock(symbol, exchange, currency))

    # ------------------------------------------------------------------ #
    # Account state
    # ------------------------------------------------------------------ #
    def account_summary(self, account: str = "") -> dict:
        """Key account figures as a plain ``{tag: value}`` dict.

        Groups by tag; when IBKR reports per-currency rows we keep the base
        summary (tag) and also expose ``Tag.CUR`` entries for detail.
        """
        self._require_connection()
        acct = account or self.account
        out: dict[str, str] = {}
        for av in self.ib.accountSummary(acct):
            out[av.tag] = av.value
            if av.currency and av.currency not in ("", "BASE"):
                out[f"{av.tag}.{av.currency}"] = av.value
        return out

    def positions(self, account: str = "") -> list[dict]:
        """Open positions as a list of dicts."""
        self._require_connection()
        acct = account or self.account
        rows = []
        for p in self.ib.positions(acct):
            rows.append({
                "account": p.account,
                "symbol": p.contract.localSymbol or p.contract.symbol,
                "secType": p.contract.secType,
                "exchange": p.contract.exchange,
                "currency": p.contract.currency,
                "position": p.position,
                "avgCost": p.avgCost,
                "conId": p.contract.conId,
            })
        return rows

    def portfolio(self, account: str = "") -> list[dict]:
        """Portfolio items (positions enriched with live P&L / market value)."""
        self._require_connection()
        acct = account or self.account
        rows = []
        for it in self.ib.portfolio(acct):
            rows.append({
                "symbol": it.contract.localSymbol or it.contract.symbol,
                "secType": it.contract.secType,
                "position": it.position,
                "marketPrice": it.marketPrice,
                "marketValue": it.marketValue,
                "averageCost": it.averageCost,
                "unrealizedPNL": it.unrealizedPNL,
                "realizedPNL": it.realizedPNL,
            })
        return rows

    # ------------------------------------------------------------------ #
    # Live quotes
    # ------------------------------------------------------------------ #
    def quote(self, contract: Contract, timeout: float = 6.0) -> dict:
        """Snapshot bid / ask / last for a contract.

        Streams market data briefly until bid & ask populate (or ``timeout``),
        then cancels the subscription. For FX the "last" price is usually
        empty, so bid/ask/mid are what you want.
        """
        self._require_connection()
        ticker: Ticker = self.ib.reqMktData(contract, "", False, False)
        deadline = timeout
        step = 0.25
        try:
            while deadline > 0:
                self.ib.sleep(step)
                deadline -= step
                if _is_price(ticker.bid) and _is_price(ticker.ask):
                    break
        finally:
            self.ib.cancelMktData(contract)

        bid = ticker.bid if _is_price(ticker.bid) else None
        ask = ticker.ask if _is_price(ticker.ask) else None
        mid = (bid + ask) / 2 if bid is not None and ask is not None else None
        return {
            "symbol": contract.localSymbol or contract.symbol,
            "bid": bid,
            "ask": ask,
            "last": ticker.last if _is_price(ticker.last) else None,
            "close": ticker.close if _is_price(ticker.close) else None,
            "mid": mid,
            "spread": (ask - bid) if bid is not None and ask is not None else None,
            "time": ticker.time,
        }

    # ------------------------------------------------------------------ #
    # Historical data
    # ------------------------------------------------------------------ #
    def history(
        self,
        contract: Contract,
        *,
        bar_size: str = "5 mins",
        duration: str = "1 M",
        what_to_show: str = "MIDPOINT",
        end: Union[datetime, str] = "",
        use_rth: bool = False,
    ):
        """A single ``reqHistoricalData`` call, returned as a DataFrame.

        ``what_to_show`` defaults to ``MIDPOINT`` which is the right choice for
        FX (TRADES is not available on IDEALPRO). For stocks use ``TRADES``.
        """
        self._require_connection()
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime=end,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=2,  # UTC-aware datetimes
        )
        return _bars_to_df(bars)

    def history_deep(
        self,
        contract: Contract,
        *,
        bar_size: str = "5 mins",
        what_to_show: str = "MIDPOINT",
        use_rth: bool = False,
        chunk: Optional[str] = None,
        max_requests: int = 240,
        pause: float = 10.0,
        stop_at: Optional[datetime] = None,
        end: Union[datetime, str] = "",
    ):
        """Walk backwards in time, one chunk per request, as deep as IBKR
        allows (or until ``stop_at`` / ``max_requests``).

        ``end`` is where the walk starts (defaults to "now"); pass an earlier
        datetime to back-fill history older than an existing cache.

        IBKR pacing: no more than 60 historical requests per 10 minutes, so we
        sleep ``pause`` seconds between calls (default 10s -> <=60 in 10 min).
        Returns the concatenated, de-duplicated, time-sorted DataFrame.
        """
        self._require_connection()
        label = _bar_label(bar_size)
        chunk = chunk or _CHUNK_BY_BAR.get(label, "1 M")
        stop_at = _as_utc(stop_at) if stop_at else None

        frames = []
        prev_earliest: Optional[datetime] = None

        for i in range(max_requests):
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime=end,
                durationStr=chunk,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=2,
            )
            if not bars:
                log.info("history_deep: IBKR returned no more data (request %d).", i + 1)
                break

            earliest = _as_utc(_to_dt(bars[0].date))
            frames.append(_bars_to_df(bars))
            log.info("history_deep: request %d -> %d bars back to %s",
                     i + 1, len(bars), earliest.isoformat())

            # No progress => we've hit the start of available history.
            if prev_earliest is not None and earliest >= prev_earliest:
                log.info("history_deep: no further history available.")
                break
            prev_earliest = earliest

            if stop_at is not None and earliest <= stop_at:
                break

            end = bars[0].date  # next request ends where this one began
            if i < max_requests - 1:
                self.ib.sleep(pause)

        if not frames:
            return _empty_df()
        import pandas as pd
        df = pd.concat(frames, ignore_index=True)
        return _dedupe_sort(df)

    def history_cached(
        self,
        contract: Contract,
        *,
        bar_size: str = "5 mins",
        what_to_show: str = "MIDPOINT",
        use_rth: bool = False,
        label: Optional[str] = None,
        deep: bool = True,
        **deep_kwargs,
    ):
        """Load bars from ``data/<LABEL>_<BAR>.csv`` and update incrementally.

        * First run (no cache): full deep back-fill to the start of IBKR
          history (respects ``deep=False`` to fetch only one chunk).
        * Later runs: fetch only bars newer than the last cached one, then
          merge, de-dupe and re-save.

        The CSV is the single source of truth the backtester reads via
        :meth:`load_cached`.
        """
        self._require_connection()
        label = label or (contract.localSymbol or contract.symbol).replace(".", "")
        path = self._cache_path(label, bar_size)
        existing = _read_csv(path)

        if existing is not None and len(existing):
            last = _as_utc(existing["date"].max().to_pydatetime())
            log.info("Cache %s: %d rows, last bar %s -> incremental update.",
                     path.name, len(existing), last.isoformat())
            frames = [existing]
            frames.append(self.history_deep(
                contract, bar_size=bar_size, what_to_show=what_to_show,
                use_rth=use_rth, stop_at=last, **deep_kwargs,
            ))
            if deep:
                # Also extend the cache *backwards*: a cache started with
                # deep=False would otherwise never reach older history.
                earliest = _as_utc(existing["date"].min().to_pydatetime())
                log.info("Cache %s: back-filling older than %s.",
                         path.name, earliest.isoformat())
                frames.append(self.history_deep(
                    contract, bar_size=bar_size, what_to_show=what_to_show,
                    use_rth=use_rth, end=earliest, **deep_kwargs,
                ))
            import pandas as pd
            combined = pd.concat(frames, ignore_index=True)
            df = _dedupe_sort(combined)
        elif deep:
            log.info("Cache %s: empty -> full deep back-fill.", path.name)
            df = self.history_deep(
                contract, bar_size=bar_size, what_to_show=what_to_show,
                use_rth=use_rth, **deep_kwargs,
            )
        else:
            df = self.history(contract, bar_size=bar_size,
                              what_to_show=what_to_show, use_rth=use_rth)

        # Never overwrite a good cache with an empty fetch (no permissions,
        # market closed, pacing violation…): that silently destroys history.
        if not len(df):
            log.warning("Cache %s: fetch returned 0 rows -> keeping existing "
                        "file untouched.", path.name)
            return existing if existing is not None else df

        self._write_csv(path, df)
        log.info("Cache %s saved: %d rows (%s .. %s).", path.name, len(df),
                 df["date"].min(), df["date"].max())
        return df

    # ------------------------------------------------------------------ #
    # Cache helpers (also usable offline by the backtester)
    # ------------------------------------------------------------------ #
    def _cache_path(self, label: str, bar_size: str) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir / f"{CACHE_PREFIX}{label}_{_bar_label(bar_size)}.csv"

    def _write_csv(self, path: Path, df) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)

    @staticmethod
    def load_cached(
        label: str,
        bar_size: str = "5 mins",
        data_dir: Union[str, Path] = DEFAULT_DATA_DIR,
    ):
        """Read cached bars from CSV **without any network connection**.

        This is what the backtester calls: ``load_cached("EURUSD", "5 mins")``.
        Returns an empty DataFrame if the cache does not exist yet.
        """
        path = Path(data_dir) / f"{CACHE_PREFIX}{label}_{_bar_label(bar_size)}.csv"
        df = _read_csv(path)
        return df if df is not None else _empty_df()

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #
    def market_order(self, contract: Contract, action: str, quantity: float):
        """Submit a market order. ``action`` is ``"BUY"`` or ``"SELL"``."""
        self._require_connection()
        return self._place(contract, MarketOrder(action.upper(), quantity))

    def limit_order(self, contract: Contract, action: str, quantity: float,
                    limit_price: float):
        self._require_connection()
        return self._place(
            contract, LimitOrder(action.upper(), quantity, limit_price))

    def stop_order(self, contract: Contract, action: str, quantity: float,
                   stop_price: float):
        self._require_connection()
        return self._place(
            contract, StopOrder(action.upper(), quantity, stop_price))

    def _place(self, contract: Contract, order):
        trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(0.2)  # let the initial status flow in
        log.info("Placed %s %s x%s -> orderId=%s status=%s",
                 order.action, contract.symbol, order.totalQuantity,
                 trade.order.orderId, trade.orderStatus.status)
        return trade

    def cancel(self, order_or_trade) -> None:
        """Cancel an order given a ``Trade`` or an ``Order``."""
        self._require_connection()
        order = getattr(order_or_trade, "order", order_or_trade)
        self.ib.cancelOrder(order)
        log.info("Cancel requested for orderId=%s", order.orderId)

    def cancel_all(self) -> None:
        """Cancel every open order (global)."""
        self._require_connection()
        self.ib.reqGlobalCancel()

    def open_orders(self) -> list[dict]:
        self._require_connection()
        self.ib.reqOpenOrders()
        self.ib.sleep(0.2)
        rows = []
        for t in self.ib.openTrades():
            rows.append({
                "orderId": t.order.orderId,
                "symbol": t.contract.localSymbol or t.contract.symbol,
                "action": t.order.action,
                "quantity": t.order.totalQuantity,
                "orderType": t.order.orderType,
                "lmtPrice": t.order.lmtPrice,
                "status": t.orderStatus.status,
                "filled": t.orderStatus.filled,
                "remaining": t.orderStatus.remaining,
            })
        return rows


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #
def _is_num(x) -> bool:
    """True if x is a real, finite number (IBKR uses NaN for 'no data')."""
    try:
        return x is not None and x == x and abs(float(x)) != float("inf")
    except (TypeError, ValueError):
        return False


def _is_price(x) -> bool:
    """True for a usable price. IBKR reports NaN or -1 when no quote is
    available, so any non-positive value is treated as 'no data'."""
    return _is_num(x) and float(x) > 0


def _to_dt(value) -> datetime:
    """Coerce a bar ``date`` (datetime or date) to a datetime."""
    if isinstance(value, datetime):
        return value
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _bars_to_df(bars: Iterable):
    """BarDataList -> tidy DataFrame with a normalised ``date`` column."""
    df = util.df(bars)
    if df is None or len(df) == 0:
        return _empty_df()
    keep = [c for c in ["date", "open", "high", "low", "close", "volume",
                        "average", "barCount"] if c in df.columns]
    df = df[keep].copy()
    import pandas as pd
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df


def _empty_df():
    import pandas as pd
    return pd.DataFrame(
        columns=["date", "open", "high", "low", "close", "volume"])


def _dedupe_sort(df):
    if df is None or len(df) == 0:
        return _empty_df()
    import pandas as pd
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = (df.drop_duplicates(subset="date", keep="last")
            .sort_values("date")
            .reset_index(drop=True))
    return df


def _read_csv(path: Path):
    if not path.exists():
        return None
    import pandas as pd
    df = pd.read_csv(path)
    if "date" not in df.columns or len(df) == 0:
        return None
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df
