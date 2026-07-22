"""Oanda v20 REST adaptér — practice endpoint, čisté stdlib (urllib).

Použitie:
    from trading.broker_oanda import OandaBroker
    ob = OandaBroker(token, account_id)          # practice natvrdo
    ob.account_summary()                          # NAV, balance, margin
    ob.price()                                    # bid/ask/mid EUR_USD
    ob.candles_m5(600)                            # M5 história (mid)
    ob.market_order_with_tp(-2000, 1.14064, tag="Grid25-G2B-O")
    ob.open_trades()                              # otvorené obchody
    ob.trade("123")                               # detail (state, realizedPL,
                                                  #  financing, closePrice)

Poznámky:
* units: kladné = BUY (long), záporné = SELL (short).
* TP sa zadáva atomicky cez takeProfitOnFill — netreba spravovať
  samostatné TP objednávky ako na IBKR.
* realizedPL a financing v odpovediach sú v MENE ÚČTU.
* Live endpoint vyžaduje practice=False — bot_oanda.py to podmieňuje
  vedomým potvrdením cez env, rovnako ako IBKR bot.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

PRACTICE_HOST = "https://api-fxpractice.oanda.com"
LIVE_HOST = "https://api-fxtrade.oanda.com"


class OandaError(Exception):
    """Chyba Oanda API (HTTP alebo odmietnutá objednávka)."""


class OandaBroker:
    def __init__(self, token: str, account_id: str, *,
                 practice: bool = True, instrument: str = "EUR_USD",
                 timeout: float = 20.0):
        if not token or not account_id:
            raise OandaError("Chýba OANDA_API_TOKEN / OANDA_ACCOUNT_ID.")
        self.host = PRACTICE_HOST if practice else LIVE_HOST
        self.token = token
        self.account_id = account_id
        self.instrument = instrument
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        url = self.host + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise OandaError(f"{method} {path} -> HTTP {e.code}: {detail}") from None
        except OSError as e:
            raise OandaError(f"{method} {path} -> {e}") from None

    # ------------------------------------------------------------------ #
    def account_summary(self) -> dict:
        """{'NAV': float, 'balance': float, 'marginUsed': float,
        'currency': str, 'openTradeCount': int}"""
        a = self._req("GET", f"/v3/accounts/{self.account_id}/summary")["account"]
        return {
            "NAV": float(a["NAV"]),
            "balance": float(a["balance"]),
            "marginUsed": float(a.get("marginUsed", 0)),
            "unrealizedPL": float(a.get("unrealizedPL", 0)),
            "currency": a.get("currency", ""),
            "openTradeCount": int(a.get("openTradeCount", 0)),
        }

    def price(self) -> dict:
        d = self._req("GET", f"/v3/accounts/{self.account_id}/pricing"
                             f"?instruments={self.instrument}")
        p = d["prices"][0]
        bid = float(p["bids"][0]["price"])
        ask = float(p["asks"][0]["price"])
        return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2,
                "spread": ask - bid, "time": p.get("time", ""),
                "tradeable": p.get("tradeable", True)}

    def candles_m5(self, count: int = 600) -> list[dict]:
        """Uzavreté M5 sviečky (mid): [{'time','o','h','l','c'}, ...]."""
        d = self._req("GET", f"/v3/instruments/{self.instrument}/candles"
                             f"?granularity=M5&count={min(count, 5000)}&price=M")
        out = []
        for c in d.get("candles", []):
            if not c.get("complete"):
                continue
            m = c["mid"]
            out.append({"time": c["time"], "o": float(m["o"]),
                        "h": float(m["h"]), "l": float(m["l"]),
                        "c": float(m["c"])})
        return out

    # ------------------------------------------------------------------ #
    def market_order_with_tp(self, units: float, tp_price: float,
                             tag: str = "") -> dict:
        """MARKET order s pripojeným TP (GTC). Vráti
        {'trade_id', 'price', 'commission'} alebo vyhodí OandaError."""
        body = {"order": {
            "type": "MARKET",
            "instrument": self.instrument,
            "units": str(int(units)),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "takeProfitOnFill": {"price": f"{tp_price:.5f}",
                                 "timeInForce": "GTC"},
        }}
        if tag:
            body["order"]["tradeClientExtensions"] = {"tag": tag[:128]}
        d = self._req("POST", f"/v3/accounts/{self.account_id}/orders", body)
        fill = d.get("orderFillTransaction")
        if not fill:
            reason = (d.get("orderCancelTransaction") or {}).get("reason", "?")
            raise OandaError(f"Objednávka nevyplnená: {reason}")
        opened = fill.get("tradeOpened") or {}
        return {
            "trade_id": opened.get("tradeID", ""),
            "price": float(fill["price"]),
            "commission": abs(float(fill.get("commission", 0) or 0)),
        }

    def open_trades(self) -> list[dict]:
        return self._req("GET",
                         f"/v3/accounts/{self.account_id}/openTrades")["trades"]

    def trade(self, trade_id: str) -> dict:
        """Detail obchodu vrátane state (OPEN/CLOSED), realizedPL,
        financing a averageClosePrice (mena účtu)."""
        return self._req("GET",
                         f"/v3/accounts/{self.account_id}/trades/{trade_id}")["trade"]

    def close_trade(self, trade_id: str) -> dict:
        """Núdzové ručné zavretie (bot ho bežne nepoužíva — TP je GTC)."""
        return self._req("PUT",
                         f"/v3/accounts/{self.account_id}/trades/{trade_id}/close")
