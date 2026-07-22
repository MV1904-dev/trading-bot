#!/usr/bin/env python3
"""cTrader smoke test (obdoba trading/smoke_test.py pre IBKR).

Spusti, keď sú v .env kľúče zo schválenej Spotware aplikácie:
    python3 test_ctrader.py

Kroky:
1. pripojenie na demo endpoint + app auth,
2. ak CTRADER_ACCOUNT_ID chýba → vypíše účty dostupné pre access token
   (pomôcka na zistenie ID) a skončí,
3. account auth + stav účtu,
4. živý bid/ask EURUSD zo streamu,
5. M5 história (posledných ~50 trendbarov).

Exit kód 0 len ak VŠETKO prešlo (žiadna falošná zelená).
"""

from __future__ import annotations

import logging
import os
import sys
import time

from dotenv import load_dotenv

from trading.broker_ctrader import CTraderBroker, CTraderError


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_dotenv()
    cid = os.getenv("CTRADER_CLIENT_ID", "")
    secret = os.getenv("CTRADER_CLIENT_SECRET", "")
    token = os.getenv("CTRADER_ACCESS_TOKEN", "")
    acct = os.getenv("CTRADER_ACCOUNT_ID", "")
    if not cid or cid.startswith("<") or not secret or not token:
        print("CHYBA: V .env chýbajú CTRADER_CLIENT_ID / CLIENT_SECRET / "
              "ACCESS_TOKEN.", file=sys.stderr)
        return 1

    # --- 1) + 2): bez account ID vypíš dostupné účty -----------------------
    if not acct or acct.startswith("<"):
        print("CTRADER_ACCOUNT_ID nie je vyplnené — vypisujem účty pre token:")
        broker = CTraderBroker(cid, secret, token, "", demo=True)
        broker.connect()
        try:
            accounts = broker.account_list()
        finally:
            broker.disconnect()
        if not accounts:
            print("  (token nemá priradené žiadne účty — over scope "
                  "'trading' pri generovaní tokenu)", file=sys.stderr)
            return 1
        for a in accounts:
            print(f"  ctidTraderAccountId={a['ctidTraderAccountId']}  "
                  f"live={a['isLive']}  login={a['traderLogin']}")
        print("\nDemo účet (live=False) vlož do .env ako CTRADER_ACCOUNT_ID "
              "a spusti test znova.")
        return 1

    # --- 3) plný test -------------------------------------------------------
    broker = CTraderBroker(cid, secret, token, acct, demo=True)
    try:
        broker.connect()
        print("\n=== ÚČET (demo) ===")
        summary = broker.account_summary()
        print(f"  balance: {summary['balance']:,.2f} | "
              f"leverage: 1:{summary['leverageInCents'] // 100 or '?'}")

        print("\n=== ŽIVÁ KOTÁCIA EURUSD ===")
        q = None
        for _ in range(30):                    # stream potrebuje pár sekúnd
            q = broker.quote()
            if q:
                break
            time.sleep(1)
        if not q:
            print("  ✗ Žiadny spot do 30 s (trh zavretý? subscription "
                  "zlyhala?)", file=sys.stderr)
            return 1
        print(f"  bid={q['bid']:.5f}  ask={q['ask']:.5f}  "
              f"spread={q['spread'] * 1e4:.2f} pipu")

        print("\n=== M5 HISTÓRIA ===")
        candles = broker.candles_m5(50)
        if not candles:
            print("  ✗ Trendbary neprišli.", file=sys.stderr)
            return 1
        first = time.strftime("%Y-%m-%d %H:%M", time.gmtime(candles[0]["time"]))
        last = time.strftime("%Y-%m-%d %H:%M", time.gmtime(candles[-1]["time"]))
        print(f"  barov: {len(candles)} | {first} → {last} UTC")
        for c in candles[-3:]:
            t = time.strftime("%H:%M", time.gmtime(c["time"]))
            print(f"  {t}  O {c['o']:.5f}  H {c['h']:.5f}  "
                  f"L {c['l']:.5f}  C {c['c']:.5f}")

        print("\n✓ cTrader smoke test prešiel.")
        return 0
    except CTraderError as exc:
        print(f"\n✗ cTrader smoke test ZLYHAL: {exc}", file=sys.stderr)
        return 1
    finally:
        broker.disconnect()


if __name__ == "__main__":
    sys.exit(main())
