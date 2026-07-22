"""cTrader Open API adaptér (Spotware, demo endpoint) — rozhranie v duchu
broker_ibkr.py / broker_oanda.py.

Technika: oficiálne SDK ``ctrader-open-api`` (Protobuf cez TLS, Twisted).
Twisted reactor beží v démonovom vlákne; verejné metódy sú synchrónne
(mostík cez reactor.callFromThread + threading.Event), takže bot ich volá
rovnako ako pri Oande.

Auth reťazec: ProtoOAApplicationAuthReq (client id+secret) →
ProtoOAAccountAuthReq (access token + ctidTraderAccountId) → symbols →
ProtoOASubscribeSpotsReq (streaming bid/ask).

Jednotky (overené introspekciou SDK 0.9.2):
* ceny v spot/trendbar správach: int × 1e-5
* objem v ProtoOANewOrderReq: jednotky × 100 (2 000 EUR → 200 000)
* relativeTakeProfit: vzdialenosť od exekučnej ceny × 1e5
* peniaze (balance, grossProfit, swap, commission): int / 10^moneyDigits

POZOR: bez schválených kľúčov sa nedá integračne testovať — prvé reálne
overenie je test_ctrader.py; drobné odchýlky brokera (názvy symbolov,
moneyDigits) rieši defenzívne.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Optional

from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq, ProtoOAApplicationAuthReq, ProtoOADealListReq,
    ProtoOAGetAccountListByAccessTokenReq, ProtoOAGetTrendbarsReq,
    ProtoOANewOrderReq, ProtoOAReconcileReq, ProtoOASubscribeSpotsReq,
    ProtoOASymbolsListReq, ProtoOATraderReq)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAOrderType, ProtoOATradeSide, ProtoOATrendbarPeriod)

log = logging.getLogger(__name__)

PRICE_SCALE = 1e-5
VOLUME_SCALE = 100          # jednotky -> volume v požiadavke

_reactor_thread: Optional[threading.Thread] = None


def _ensure_reactor() -> None:
    """Spustí Twisted reactor v démonovom vlákne (raz na proces)."""
    global _reactor_thread
    from twisted.internet import reactor
    if _reactor_thread and _reactor_thread.is_alive():
        return
    _reactor_thread = threading.Thread(
        target=lambda: reactor.run(installSignalHandlers=False),
        name="ctrader-reactor", daemon=True)
    _reactor_thread.start()


class CTraderError(Exception):
    """Chyba cTrader Open API."""


class CTraderBroker:
    def __init__(self, client_id: str, client_secret: str, access_token: str,
                 account_id: str, *, demo: bool = True,
                 symbol_name: str = "EURUSD"):
        if not all([client_id, client_secret, access_token]):
            raise CTraderError("Chýba CTRADER_CLIENT_ID / CLIENT_SECRET / "
                               "ACCESS_TOKEN.")
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.account_id = int(account_id) if account_id else 0
        self.host = EndPoints.PROTOBUF_DEMO_HOST if demo \
            else EndPoints.PROTOBUF_LIVE_HOST
        self.symbol_name = symbol_name
        self.symbol_id: Optional[int] = None

        self._client: Optional[Client] = None
        self._app_authed = threading.Event()
        self._ready = threading.Event()
        self._bid: Optional[float] = None
        self._ask: Optional[float] = None
        self._spot_ts = 0.0
        self._execs: dict[int, object] = {}       # orderId -> ExecutionEvent
        self._exec_lock = threading.Lock()
        self._exec_queue: "queue.Queue[object]" = queue.Queue()

    # ------------------------------------------------------------------ #
    # Pripojenie
    # ------------------------------------------------------------------ #
    def connect(self, timeout: float = 45.0) -> None:
        from twisted.internet import reactor
        _ensure_reactor()
        self._client = Client(self.host, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self._client.setConnectedCallback(self._on_connected)
        self._client.setDisconnectedCallback(self._on_disconnected)
        self._client.setMessageReceivedCallback(self._on_message)
        reactor.callFromThread(self._client.startService)
        if not self._ready.wait(timeout):
            raise CTraderError(
                f"Auth nezbehol do {timeout:.0f} s "
                f"({'app' if not self._app_authed.is_set() else 'account'} "
                f"auth visí) — skontroluj kľúče/účet.")
        self._resolve_symbol()
        self._subscribe_spots()
        log.info("cTrader pripojený: %s, účet %d, %s (id %s).",
                 self.host, self.account_id, self.symbol_name, self.symbol_id)

    def disconnect(self) -> None:
        if self._client is not None:
            from twisted.internet import reactor
            reactor.callFromThread(self._client.stopService)
            self._client = None
        self._ready.clear()
        self._app_authed.clear()

    def is_connected(self) -> bool:
        return self._ready.is_set()

    # --- callbacky (bežia v reactor vlákne) --------------------------------
    def _on_connected(self, client) -> None:
        req = ProtoOAApplicationAuthReq()
        req.clientId = self.client_id
        req.clientSecret = self.client_secret
        d = client.send(req)
        d.addCallback(self._on_app_auth)
        d.addErrback(self._auth_err)

    def _on_app_auth(self, _msg) -> None:
        self._app_authed.set()
        if not self.account_id:
            self._ready.set()          # len app auth (výpis účtov v teste)
            return
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = self.account_id
        req.accessToken = self.access_token
        d = self._client.send(req)
        d.addCallback(lambda _m: self._ready.set())
        d.addErrback(self._auth_err)

    def _auth_err(self, failure) -> None:
        log.error("cTrader auth zlyhal: %s", failure)

    def _on_disconnected(self, _client, reason) -> None:
        log.warning("cTrader odpojený: %s", reason)
        self._ready.clear()

    def _on_message(self, _client, message) -> None:
        try:
            msg = Protobuf.extract(message)
        except Exception:  # noqa: BLE001 — neznáme správy ignoruj
            return
        name = type(msg).__name__
        if name == "ProtoOASpotEvent":
            if getattr(msg, "bid", 0):
                self._bid = msg.bid * PRICE_SCALE
            if getattr(msg, "ask", 0):
                self._ask = msg.ask * PRICE_SCALE
            self._spot_ts = time.time()
        elif name == "ProtoOAExecutionEvent":
            oid = getattr(getattr(msg, "order", None), "orderId", 0)
            with self._exec_lock:
                if oid:
                    self._execs[oid] = msg
            self._exec_queue.put(msg)
        elif name == "ProtoHeartbeatEvent":
            pass                        # SDK udržiava spojenie sám

    # --- synchrónny mostík ---------------------------------------------------
    def _send(self, req, timeout: float = 15.0):
        from twisted.internet import reactor
        if self._client is None:
            raise CTraderError("Nie je spojenie (connect() nezbehol).")
        done = threading.Event()
        box: dict = {}

        def op():
            d = self._client.send(req, responseTimeoutInSeconds=timeout)
            d.addCallback(lambda m: (box.__setitem__("ok", Protobuf.extract(m)),
                                     done.set()))
            d.addErrback(lambda f: (box.__setitem__("err", f), done.set()))

        reactor.callFromThread(op)
        if not done.wait(timeout + 5):
            raise CTraderError(f"{type(req).__name__}: timeout")
        if "err" in box:
            raise CTraderError(f"{type(req).__name__}: {box['err']}")
        resp = box["ok"]
        if type(resp).__name__ in ("ProtoOAErrorRes", "ProtoOAOrderErrorEvent"):
            raise CTraderError(f"{type(req).__name__} -> "
                               f"{getattr(resp, 'errorCode', '?')}: "
                               f"{getattr(resp, 'description', '')}")
        return resp

    # ------------------------------------------------------------------ #
    # Účty / symboly / dáta
    # ------------------------------------------------------------------ #
    def account_list(self) -> list[dict]:
        """Účty dostupné pre access token (na zistenie ACCOUNT_ID)."""
        req = ProtoOAGetAccountListByAccessTokenReq()
        req.accessToken = self.access_token
        res = self._send(req)
        return [{"ctidTraderAccountId": a.ctidTraderAccountId,
                 "isLive": getattr(a, "isLive", False),
                 "traderLogin": getattr(a, "traderLogin", 0)}
                for a in res.ctidTraderAccount]

    def _resolve_symbol(self) -> None:
        req = ProtoOASymbolsListReq()
        req.ctidTraderAccountId = self.account_id
        res = self._send(req, timeout=20)
        want = self.symbol_name.replace("/", "").upper()
        for s in res.symbol:
            if s.symbolName.replace("/", "").upper() == want:
                self.symbol_id = s.symbolId
                return
        raise CTraderError(f"Symbol {self.symbol_name} sa nenašiel "
                           f"({len(res.symbol)} symbolov u brokera).")

    def _subscribe_spots(self) -> None:
        req = ProtoOASubscribeSpotsReq()
        req.ctidTraderAccountId = self.account_id
        req.symbolId.append(self.symbol_id)
        self._send(req)

    def account_summary(self) -> dict:
        req = ProtoOATraderReq()
        req.ctidTraderAccountId = self.account_id
        res = self._send(req)
        t = res.trader
        digits = getattr(t, "moneyDigits", 2) or 2
        return {"balance": t.balance / 10 ** digits,
                "leverageInCents": getattr(t, "leverageInCents", 0),
                "raw": t}

    def quote(self) -> Optional[dict]:
        """Posledný spot zo streamu (None, kým nepríde prvý tick)."""
        if self._bid is None or self._ask is None:
            return None
        return {"bid": self._bid, "ask": self._ask,
                "mid": (self._bid + self._ask) / 2,
                "spread": self._ask - self._bid,
                "age_s": time.time() - self._spot_ts,
                "tradeable": self._ready.is_set()}

    def candles_m5(self, count: int = 600) -> list[dict]:
        req = ProtoOAGetTrendbarsReq()
        req.ctidTraderAccountId = self.account_id
        req.symbolId = self.symbol_id
        req.period = ProtoOATrendbarPeriod.M5
        now_ms = int(time.time() * 1000)
        req.toTimestamp = now_ms
        req.fromTimestamp = now_ms - (count + 10) * 300 * 1000
        res = self._send(req, timeout=20)
        out = []
        for tb in res.trendbar:
            low = tb.low * PRICE_SCALE
            out.append({"time": tb.utcTimestampInMinutes * 60,
                        "o": low + tb.deltaOpen * PRICE_SCALE,
                        "h": low + tb.deltaHigh * PRICE_SCALE,
                        "l": low,
                        "c": low + tb.deltaClose * PRICE_SCALE})
        return out[-count:]

    history = candles_m5          # alias v duchu broker_ibkr.history

    # ------------------------------------------------------------------ #
    # Obchodovanie
    # ------------------------------------------------------------------ #
    def market_order_with_tp(self, units: float, tp_price: float,
                             tag: str = "") -> dict:
        """MARKET order s relatívnym TP. Vráti {'position_id', 'price',
        'order_id'}; commission príde až v closing deale."""
        q = self.quote()
        if q is None:
            raise CTraderError("Bez kotácie neviem vypočítať relatívny TP.")
        req = ProtoOANewOrderReq()
        req.ctidTraderAccountId = self.account_id
        req.symbolId = self.symbol_id
        req.orderType = ProtoOAOrderType.MARKET
        req.tradeSide = ProtoOATradeSide.BUY if units > 0 \
            else ProtoOATradeSide.SELL
        req.volume = int(abs(units)) * VOLUME_SCALE
        req.relativeTakeProfit = max(int(round(abs(tp_price - q["mid"]) * 1e5)), 1)
        if tag:
            req.label = tag[:100]
        resp = self._send(req)

        oid = getattr(getattr(resp, "order", None), "orderId", 0)
        deadline = time.time() + 15
        while time.time() < deadline:
            with self._exec_lock:
                ev = self._execs.get(oid)
            if ev is not None and getattr(ev, "executionType", 0) == 3:  # ORDER_FILLED
                deal = getattr(ev, "deal", None)
                pos = getattr(ev, "position", None)
                return {
                    "position_id": getattr(pos, "positionId", 0)
                                   or getattr(deal, "positionId", 0),
                    "order_id": oid,
                    "price": getattr(deal, "executionPrice", 0.0)
                             or getattr(pos, "price", 0.0),
                }
            time.sleep(0.2)
        raise CTraderError(f"Order {oid}: fill event neprišiel do 15 s.")

    def limit_order(self, units: float, limit_price: float,
                    tp_price: float = 0.0, tag: str = "") -> dict:
        req = ProtoOANewOrderReq()
        req.ctidTraderAccountId = self.account_id
        req.symbolId = self.symbol_id
        req.orderType = ProtoOAOrderType.LIMIT
        req.tradeSide = ProtoOATradeSide.BUY if units > 0 \
            else ProtoOATradeSide.SELL
        req.volume = int(abs(units)) * VOLUME_SCALE
        req.limitPrice = limit_price
        if tp_price:
            req.takeProfit = tp_price
        if tag:
            req.label = tag[:100]
        resp = self._send(req)
        return {"order_id": getattr(getattr(resp, "order", None), "orderId", 0)}

    def positions(self) -> list[dict]:
        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = self.account_id
        res = self._send(req)
        out = []
        for p in res.position:
            td = p.tradeData
            digits = getattr(p, "moneyDigits", 2) or 2
            out.append({
                "position_id": p.positionId,
                "side": "long" if td.tradeSide == ProtoOATradeSide.BUY else "short",
                "units": td.volume / VOLUME_SCALE,
                "price": p.price,
                "swap": p.swap / 10 ** digits,
                "used_margin": getattr(p, "usedMargin", 0) / 10 ** digits,
                "label": getattr(td, "label", ""),
            })
        return out

    def open_position_ids(self) -> set:
        return {p["position_id"] for p in self.positions()}

    def closed_deals_since(self, ts_ms: int) -> dict:
        """{positionId: {'close_price','gross','swap','commission'}} pre
        zatvárajúce dealy od ts_ms (peniaze v mene účtu)."""
        req = ProtoOADealListReq()
        req.ctidTraderAccountId = self.account_id
        req.fromTimestamp = ts_ms
        req.toTimestamp = int(time.time() * 1000)
        req.maxRows = 500
        res = self._send(req, timeout=20)
        out = {}
        for d in res.deal:
            cpd = getattr(d, "closePositionDetail", None)
            if cpd is None or not getattr(cpd, "closedVolume", 0):
                continue
            digits = getattr(cpd, "moneyDigits", 2) or 2
            out[d.positionId] = {
                "close_price": d.executionPrice,
                "gross": cpd.grossProfit / 10 ** digits,
                "swap": cpd.swap / 10 ** digits,
                "commission": (abs(cpd.commission)
                               + abs(getattr(d, "commission", 0))) / 10 ** digits,
            }
        return out
