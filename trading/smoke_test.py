#!/usr/bin/env python3
"""IBKR paper-account smoke test (step 5).

Run this on the machine where IB Gateway is running (paper mode, API on
port 4002). It will:

  1. connect to the Gateway,
  2. print the paper account summary,
  3. print the live EURUSD bid / ask,
  4. deep-download EURUSD M5 history into ``data/EURUSD_M5.csv`` (cached +
     incremental on subsequent runs).

Usage::

    pip install -r trading/requirements.txt
    python -m trading.smoke_test                 # full deep back-fill
    python -m trading.smoke_test --quick         # one chunk only (fast check)
    python -m trading.smoke_test --port 7497     # against TWS instead

The first deep back-fill can take a while (IBKR paces historical requests to
~60 per 10 min); every later run only fetches the new tail.
"""

from __future__ import annotations

import argparse
import logging
import sys

from trading.broker_ibkr import IBKRBroker, _bar_label


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="IBKR paper smoke test")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4002,
                   help="4002=Gateway paper (default), 4001=Gateway live, "
                        "7497=TWS paper")
    p.add_argument("--client-id", type=int, default=17)
    p.add_argument("--pair", default="EURUSD")
    p.add_argument("--bar", default="5 mins")
    p.add_argument("--quick", action="store_true",
                   help="fetch only one recent chunk instead of deep history")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    broker = IBKRBroker(host=args.host, port=args.port, client_id=args.client_id)
    try:
        broker.connect()
    except Exception as exc:  # connection is the most common failure point
        print(f"\n✗ Could not connect to IBKR at {args.host}:{args.port}: {exc}",
              file=sys.stderr)
        print("  → Is IB Gateway running in *Paper Trading* mode with API "
              "enabled on this port? (see docs/ibkr-setup.md)", file=sys.stderr)
        return 1

    try:
        # 1) Account status ------------------------------------------------
        print("\n=== PAPER ACCOUNT ===")
        summary = broker.account_summary()
        accounts = ", ".join(broker.ib.managedAccounts()) or "?"
        print(f"Account(s): {accounts}")
        for tag in ("NetLiquidation", "TotalCashValue", "AvailableFunds",
                    "BuyingPower", "MaintMarginReq", "UnrealizedPnL"):
            if tag in summary:
                cur = summary.get("Currency", "")
                print(f"  {tag:<18} {summary[tag]:>15} {cur}")

        positions = broker.positions()
        print(f"Open positions: {len(positions)}")
        for pos in positions:
            print(f"  {pos['symbol']:<10} {pos['position']:>12} "
                  f"@ {pos['avgCost']}")

        # 2) Live quote ----------------------------------------------------
        print(f"\n=== LIVE QUOTE {args.pair} ===")
        contract = broker.forex(args.pair)
        q = broker.quote(contract)
        if q["bid"] is None or q["ask"] is None:
            print("  (no live bid/ask — market closed or no FX data "
                  "subscription; historical MIDPOINT still works)")
        print(f"  bid={q['bid']}  ask={q['ask']}  mid={q['mid']}  "
              f"spread={q['spread']}")

        # 3) M5 history, deep + cached ------------------------------------
        print(f"\n=== {args.pair} {args.bar} HISTORY -> data/ ===")
        if args.quick:
            df = broker.history_cached(contract, bar_size=args.bar, deep=False)
        else:
            df = broker.history_cached(contract, bar_size=args.bar, deep=True)

        if len(df):
            print(f"  rows:  {len(df):,}")
            print(f"  range: {df['date'].min()}  ..  {df['date'].max()}")
            print(f"  saved: data/{args.pair}_{_bar_label(args.bar)}.csv")
            print("\n  last 3 bars:")
            print(df.tail(3).to_string(index=False))
        else:
            print("  no bars returned.")

        print("\n✓ Smoke test complete.")
        return 0
    finally:
        broker.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
