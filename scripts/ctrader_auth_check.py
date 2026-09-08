#!/usr/bin/env python3
"""Diagnostika app authu voči obom endpointom Spotware.

Keď bot hlási "cTrader auth zlyhal: TimeoutError (5, 'Deferred')" a hneď
nato "Connection was closed cleanly", je otázka jediná: je problém
v aplikácii (CLIENT_ID/SECRET), alebo len na jednom endpointe? Bot beží
podľa CTRADER_DEMO a sám to nerozlíši.

Skript skúsi SAMOTNÝ app auth (clientId + clientSecret, access token sa
naň nepoužíva) proti demo aj live hostu. Kde prejde, vypíše aj účty, ktoré
má access token povolené — z toho je vidieť, či je live účet ešte v grante.

⚠️ NAJPRV ZASTAV BOTA. Spotware drží jedno app-auth spojenie naraz, takže
   bežiaci bot by výsledok skreslil:
       ssh hetzner 'systemctl stop ctrader-bot'
       ssh hetzner 'cd /home/marian/trading-bot && sudo -u marian .venv/bin/python scripts/ctrader_auth_check.py'
       ssh hetzner 'systemctl start ctrader-bot'

Cudziu aplikáciu (napr. novú, ktorá sa má overiť pred zásahom do .env)
zadáš buď cez --client-id/--client-secret, alebo postupne cez --ask:
       ssh -t hetzner 'cd /home/marian/trading-bot && sudo -u marian .venv/bin/python scripts/ctrader_auth_check.py --ask'
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from trading.broker_ctrader import CTraderBroker

TIMEOUT = 20.0


def check(label: str, demo: bool, cid: str, secret: str, token: str) -> bool:
    # account_id="" → connect() sa zastaví hneď po app authe, čo je presne
    # to, čo chceme zmerať (account auth už závisí od tokenu aj účtu).
    broker = CTraderBroker(cid, secret, token, "", demo=demo)
    print(f"\n=== {label} ({broker.host}) ===")
    try:
        broker.connect(timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — diagnostika, zaujíma nás všetko
        print(f"  ❌ app auth ZLYHAL: {exc}")
        return False
    print("  ✅ app auth OK")
    if not token:
        print("     (zoznam účtov preskakujem — bez access tokenu)")
        return True
    try:
        accounts = broker.account_list()
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️  zoznam účtov sa nedal načítať: {exc}")
        return True
    if not accounts:
        print("  ⚠️  access token nemá priradený ŽIADNY účet "
              "(prepadol grant? treba nový OAuth grant)")
        return True
    for a in accounts:
        kind = "LIVE" if a["isLive"] else "demo"
        print(f"     účet {a['ctidTraderAccountId']} ({kind}), "
              f"login {a['traderLogin']}")
    return True


def main() -> int:
    logging.basicConfig(level=logging.WARNING,
                        format="  %(levelname)s %(name)s: %(message)s")
    # Credentials sa dajú podstrčiť z príkazového riadka: novú aplikáciu
    # treba overiť PRED tým, než sa jej kľúče dostanú do .env, aby sa
    # bežiaca (hoc aj pokazená) konfigurácia nerozbila ešte viac.
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client-id", default="", help="prebije .env")
    ap.add_argument("--client-secret", default="", help="prebije .env")
    ap.add_argument("--ask", action="store_true",
                    help="spýtať sa na credentials postupne (secret sa "
                         "nezobrazuje a neostane v histórii shellu)")
    args = ap.parse_args()
    if args.ask:
        args.client_id = input("Client ID: ").strip()
        args.client_secret = getpass.getpass("Client Secret (nezobrazí sa): ").strip()
        if not args.client_id or not args.client_secret:
            print("CHYBA: prázdne Client ID alebo Secret.", file=sys.stderr)
            return 2
        print()
    # Polovica dvojice je vždy preklep, nie zámer — a ticho by to zobralo
    # druhú hodnotu z .env a otestovalo úplne inú aplikáciu, než človek
    # čaká (presne to sa už raz stalo so starou verziou skriptu).
    if bool(args.client_id) != bool(args.client_secret):
        print("CHYBA: --client-id a --client-secret sa zadávajú spolu.",
              file=sys.stderr)
        return 2

    load_dotenv()
    cid = args.client_id or os.getenv("CTRADER_CLIENT_ID", "")
    secret = args.client_secret or os.getenv("CTRADER_CLIENT_SECRET", "")
    # Access token patrí ku konkrétnej aplikácii — pri cudzích kľúčoch
    # by sa ním nemalo zmysel oháňať, app auth ho aj tak nepoužíva.
    token = "" if args.client_id else os.getenv("CTRADER_ACCESS_TOKEN", "")
    if not cid or not secret:
        print("CHYBA: v .env chýba CTRADER_CLIENT_ID / CLIENT_SECRET.",
              file=sys.stderr)
        return 2
    want_demo = os.getenv("CTRADER_DEMO", "1") != "0"
    print(f"CTRADER_DEMO={'1' if want_demo else '0'} → bot beží proti "
          f"{'DEMO' if want_demo else 'LIVE'} endpointu.")
    print(f"CLIENT_ID … {cid[-6:]}"
          + (" (z príkazového riadka)" if args.client_id else "")
          + f", ACCESS_TOKEN {'je nastavený' if token else 'nepoužije sa'}")

    ok_demo = check("DEMO", True, cid, secret, token)
    ok_live = check("LIVE", False, cid, secret, token)

    print("\n--- záver ---")
    if ok_demo and ok_live:
        print("Oba endpointy odpovedajú → aplikácia je v poriadku a problém")
        print("bol prechodný (alebo je inde: account auth / token / účet).")
    elif ok_demo and not ok_live:
        print("Demo prejde, LIVE nie → aplikácia nemá (už) prístup na live,")
        print("alebo má Spotware výpadok live brány. Bot má CTRADER_DEMO=0,")
        print("takže presne toto ho drží mimo. Over stav aplikácie na")
        print("https://openapi.ctrader.com/apps a grant pre live účet")
        print("(scripts/ctrader_live_grant.sh).")
    elif ok_live and not ok_demo:
        print("Live prejde, demo nie → pre bota s CTRADER_DEMO=0 je to OK,")
        print("problém bol teda inde než v app authe.")
    else:
        print("NEPREJDE ANI JEDEN endpoint → nejde o endpoint, ale o samotnú")
        print("aplikáciu alebo o sieť zo servera. Over CLIENT_ID/SECRET a to,")
        print("či server vidí von na port 5035.")
    return 0 if (ok_demo or ok_live) else 1


if __name__ == "__main__":
    raise SystemExit(main())
