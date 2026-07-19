"""Test celého obchodného cyklu na XTB demo účte (xAPI).

Skript overí kompletný „round-trip" obchod na demo účte XTB:

  1. Prihlásenie cez xAPI (údaje z .env).
  2. getSymbol pre BITCOIN – bid/ask, spread, minimálny objem (lotMin).
  3. Posledných 20 päťminútových sviečok (getChartLastRequest).
  4. Otvorenie minimálneho BUY obchodu (lotMin) so SL -1 % a TP +2 %.
  5. Overenie stavu obchodu cez tradeTransactionStatus (+ číslo obchodu).
  6. Čakanie 30 s, zatvorenie pozície a výpis zisku/straty.
  7. Odhlásenie.

POZOR: Skript zadáva REÁLNU (aj keď demo) obchodnú transakciu. Spúšťaj ho
výhradne na DEMO účte. Dokumentácia xAPI: http://developers.xstore.pro/documentation/
"""

import json
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv
from websocket import create_connection, WebSocketException

# --- Konfigurácia -----------------------------------------------------------
XTB_DEMO_URL = "wss://ws.xtb.com/demo"
SYMBOL = "BITCOIN"
CONNECT_TIMEOUT = 20           # timeout pre spojenie a požiadavky (s)
API_PAUSE = 0.3                # rozostup medzi príkazmi (XTB limituje ~1 req/200 ms)
CANDLE_PERIOD_MIN = 5          # perióda sviečky v minútach (M5)
CANDLE_COUNT = 20              # koľko posledných sviečok vypísať
HOLD_SECONDS = 30             # ako dlho držať pozíciu pred zatvorením
SL_PCT = 0.01                  # stop loss -1 %
TP_PCT = 0.02                  # take profit +2 %

# tradeTransaction – číselníky
CMD_BUY = 0
TYPE_OPEN = 0
TYPE_CLOSE = 2

# tradeTransactionStatus – requestStatus
REQUEST_STATUS = {
    0: "CHYBA (ERROR)",
    1: "ČAKÁ (PENDING)",
    3: "PRIJATÝ (ACCEPTED)",
    4: "ZAMIETNUTÝ (REJECTED)",
}


class XtbError(Exception):
    """Chyba vrátená serverom xAPI (status == false)."""


def log(message):
    """Zrozumiteľný log s časovou pečiatkou."""
    print(f"[{datetime.now():%H:%M:%S}] {message}")


def send(ws, command, arguments=None):
    """Pošle príkaz a vráti dekódovanú JSON odpoveď (bez kontroly statusu)."""
    payload = {"command": command}
    if arguments is not None:
        payload["arguments"] = arguments
    ws.send(json.dumps(payload))
    response = json.loads(ws.recv())
    time.sleep(API_PAUSE)
    return response


def call(ws, command, arguments=None):
    """Pošle príkaz a vráti returnData; pri status == false vyhodí XtbError."""
    response = send(ws, command, arguments)
    if not response.get("status"):
        code = response.get("errorCode", "N/A")
        desc = response.get("errorDescr", "neznáma chyba")
        raise XtbError(f"Príkaz '{command}' zlyhal (kód {code}): {desc}")
    return response.get("returnData")


def format_candles(chart_data):
    """Prevedie a vypíše sviečky z getChartLastRequest.

    XTB vracia ceny ako celé čísla; reálna hodnota = hodnota / 10^digits.
    Polia close/high/low sú posun (shift) voči open, preto sa k open pripočítajú.
    """
    digits = chart_data.get("digits", 0)
    factor = 10 ** digits
    rate_infos = chart_data.get("rateInfos", [])
    last = rate_infos[-CANDLE_COUNT:]

    log(f"Posledných {len(last)} sviečok M{CANDLE_PERIOD_MIN} (digits={digits}):")
    print(f"  {'Čas':<19} {'Open':>12} {'High':>12} {'Low':>12} {'Close':>12} {'Objem':>10}")
    for c in last:
        o = c["open"] / factor
        h = (c["open"] + c["high"]) / factor
        low = (c["open"] + c["low"]) / factor
        cl = (c["open"] + c["close"]) / factor
        print(
            f"  {c.get('ctmString', ''):<19} "
            f"{o:>12.2f} {h:>12.2f} {low:>12.2f} {cl:>12.2f} {c.get('vol', 0):>10.2f}"
        )


def find_new_position(trades, known_positions):
    """Nájde novootvorenú BITCOIN pozíciu, ktorá pred obchodom neexistovala."""
    candidates = [
        t for t in (trades or [])
        if t.get("symbol") == SYMBOL and t.get("position") not in known_positions
    ]
    if not candidates:
        return None
    # najnovšia podľa času otvorenia
    return max(candidates, key=lambda t: t.get("open_time", 0))


def run(ws):
    """Vykoná celý obchodný cyklus na už pripojenom WebSockete."""
    load_dotenv()
    user_id = os.getenv("XTB_USER_ID")
    password = os.getenv("XTB_PASSWORD")
    if not user_id or not password:
        raise XtbError("V .env chýba XTB_USER_ID alebo XTB_PASSWORD.")

    # --- 1) Prihlásenie -----------------------------------------------------
    log("Prihlasujem sa na XTB demo…")
    call(ws, "login", {"userId": user_id, "password": password})
    log("Prihlásenie úspešné.")

    # --- 2) getSymbol -------------------------------------------------------
    info = call(ws, "getSymbol", {"symbol": SYMBOL})
    bid = info["bid"]
    ask = info["ask"]
    lot_min = info["lotMin"]
    precision = int(info.get("precision", 2))
    spread = round(ask - bid, precision)
    log(f"Symbol {SYMBOL}:")
    print(f"    Bid:            {bid}")
    print(f"    Ask:            {ask}")
    print(f"    Spread:         {spread}")
    print(f"    Min. objem:     {lot_min} lot")

    # --- 3) getChartLastRequest (20x M5) ------------------------------------
    # Vyžiadame o niečo dlhší interval a zoberieme posledných CANDLE_COUNT sviečok.
    span_ms = (CANDLE_COUNT + 10) * CANDLE_PERIOD_MIN * 60 * 1000
    start_ms = int(time.time() * 1000) - span_ms
    chart = call(ws, "getChartLastRequest", {
        "info": {"period": CANDLE_PERIOD_MIN, "start": start_ms, "symbol": SYMBOL}
    })
    format_candles(chart)

    # --- 4) Otvorenie BUY obchodu (lotMin, SL -1 %, TP +2 %) ----------------
    sl_price = round(ask * (1 - SL_PCT), precision)
    tp_price = round(ask * (1 + TP_PCT), precision)
    log(
        f"Otváram BUY {lot_min} lot {SYMBOL} @ ~{ask} "
        f"(SL {sl_price} = -{SL_PCT:.0%}, TP {tp_price} = +{TP_PCT:.0%})…"
    )

    # zapamätáme si existujúce pozície, aby sme spoľahlivo našli tú novú
    before = call(ws, "getTrades", {"openedOnly": True}) or []
    known_positions = {t.get("position") for t in before if t.get("symbol") == SYMBOL}

    open_info = {
        "cmd": CMD_BUY,
        "type": TYPE_OPEN,
        "symbol": SYMBOL,
        "volume": lot_min,
        "price": ask,
        "sl": sl_price,
        "tp": tp_price,
        "order": 0,
        "offset": 0,
        "expiration": 0,
        "customComment": "test_trade.py – otvorenie",
    }
    open_res = call(ws, "tradeTransaction", {"tradeTransInfo": open_info})
    open_order_id = open_res["order"]
    log(f"Príkaz na otvorenie odoslaný, transakcia č. {open_order_id}.")

    # --- 5) Overenie stavu cez tradeTransactionStatus -----------------------
    status = call(ws, "tradeTransactionStatus", {"order": open_order_id})
    req = status.get("requestStatus")
    log(f"Stav obchodu č. {open_order_id}: {REQUEST_STATUS.get(req, req)}")
    if req != 3:
        raise XtbError(
            f"Obchod nebol prijatý (stav {REQUEST_STATUS.get(req, req)}): "
            f"{status.get('message') or 'bez správy'}"
        )
    log("Obchod bol PRIJATÝ.")

    # nájdeme konkrétnu otvorenú pozíciu
    time.sleep(1)
    trades = call(ws, "getTrades", {"openedOnly": True})
    position = find_new_position(trades, known_positions)
    if position is None:
        log("UPOZORNENIE: Otvorenú pozíciu sa nepodarilo nájsť v getTrades "
            "(možno ju hneď zavrel SL/TP). Preskakujem manuálne zatvorenie.")
        return
    position_id = position.get("position")
    open_price = position.get("open_price")
    log(f"Otvorená pozícia č. {position_id} @ {open_price}, objem {position.get('volume')} lot.")

    # --- 6) Čakanie a zatvorenie -------------------------------------------
    log(f"Držím pozíciu {HOLD_SECONDS} s…")
    time.sleep(HOLD_SECONDS)

    # aktuálna cena pre zatvorenie (BUY zatvárame na bide)
    cur = call(ws, "getSymbol", {"symbol": SYMBOL})
    close_price = cur["bid"]
    # floating zisk tesne pred zatvorením (pre prípad, že by história meškala)
    open_now = next(
        (t for t in (call(ws, "getTrades", {"openedOnly": True}) or [])
         if t.get("position") == position_id),
        None,
    )
    floating_profit = open_now.get("profit") if open_now else None

    log(f"Zatváram pozíciu č. {position_id} @ ~{close_price}…")
    close_info = {
        "cmd": position.get("cmd", CMD_BUY),
        "type": TYPE_CLOSE,
        "symbol": SYMBOL,
        "volume": position.get("volume", lot_min),
        "price": close_price,
        "order": position.get("order"),
        "offset": 0,
        "expiration": 0,
        "customComment": "test_trade.py – zatvorenie",
    }
    close_res = call(ws, "tradeTransaction", {"tradeTransInfo": close_info})
    close_order_id = close_res["order"]
    close_status = call(ws, "tradeTransactionStatus", {"order": close_order_id})
    creq = close_status.get("requestStatus")
    log(f"Stav zatvorenia č. {close_order_id}: {REQUEST_STATUS.get(creq, creq)}")
    if creq != 3:
        raise XtbError(
            f"Zatvorenie nebolo prijaté (stav {REQUEST_STATUS.get(creq, creq)}): "
            f"{close_status.get('message') or 'bez správy'}"
        )

    # --- Realizovaný výsledok z histórie -----------------------------------
    time.sleep(2)
    now_ms = int(time.time() * 1000)
    history = call(ws, "getTradesHistory", {"start": now_ms - 3600 * 1000, "end": 0})
    closed = next(
        (t for t in (history or []) if t.get("position") == position_id),
        None,
    )
    if closed is not None and closed.get("profit") is not None:
        profit = closed["profit"]
        source = "realizovaný (z histórie)"
        entry, exit_ = closed.get("open_price"), closed.get("close_price")
    else:
        profit = floating_profit
        source = "približný (posledný floating P/L)"
        entry, exit_ = open_price, close_price

    currency = info.get("currencyProfit") or info.get("currency") or ""
    log("=== VÝSLEDOK OBCHODU ===")
    print(f"    Pozícia č.:     {position_id}")
    print(f"    Vstup / výstup: {entry} → {exit_}")
    if profit is None:
        print("    Zisk/strata:    nezistený")
    else:
        znak = "ZISK" if profit >= 0 else "STRATA"
        print(f"    Zisk/strata:    {profit:+.2f} {currency}  ({znak}, {source})")


def main():
    ws = None
    try:
        try:
            ws = create_connection(XTB_DEMO_URL, timeout=CONNECT_TIMEOUT)
        except (WebSocketException, OSError) as exc:
            print(f"CHYBA: Nepodarilo sa pripojiť na {XTB_DEMO_URL}: {exc}")
            return 1

        log(f"Pripojené na {XTB_DEMO_URL}")
        run(ws)
        return 0

    except XtbError as exc:
        print(f"CHYBA: {exc}")
        return 1
    except (WebSocketException, OSError) as exc:
        print(f"CHYBA: Zlyhala komunikácia so serverom: {exc}")
        return 1
    except json.JSONDecodeError as exc:
        print(f"CHYBA: Server vrátil neplatnú odpoveď: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nPrerušené používateľom.")
        return 130
    finally:
        # --- 7) Odhlásenie a uzavretie spojenia ----------------------------
        if ws is not None:
            try:
                send(ws, "logout")
                log("Odhlásené.")
            except Exception as exc:  # noqa: BLE001 – logout nesmie zhodiť cleanup
                print(f"UPOZORNENIE: Odhlásenie sa nepodarilo dokončiť: {exc}")
            finally:
                ws.close()
                log("Spojenie uzavreté.")


if __name__ == "__main__":
    sys.exit(main())
