"""Test pripojenia k XTB xAPI (demo).

Skript sa cez WebSocket pripojí na demo server XTB, prihlási sa údajmi
z .env súboru, načíta aktuálny bid/ask pre EURUSD a korektne sa odhlási.

Dokumentácia xAPI: http://developers.xstore.pro/documentation/
"""

import json
import os
import sys

from dotenv import load_dotenv
from websocket import create_connection, WebSocketException

# Adresa demo WebSocket endpointu XTB
XTB_DEMO_URL = "wss://ws.xtb.com/demo"

# Timeout (v sekundách) pre nadviazanie spojenia a jednotlivé požiadavky
CONNECT_TIMEOUT = 15


def send_command(ws, command, arguments=None):
    """Pošle príkaz na server a vráti dekódovanú JSON odpoveď.

    Každý príkaz xAPI má tvar {"command": ..., "arguments": {...}}.
    """
    payload = {"command": command}
    if arguments is not None:
        payload["arguments"] = arguments

    ws.send(json.dumps(payload))
    response = json.loads(ws.recv())
    return response


def main():
    # Načítanie prihlasovacích údajov z .env
    load_dotenv()
    user_id = os.getenv("XTB_USER_ID")
    password = os.getenv("XTB_PASSWORD")

    if not user_id or not password:
        print(
            "CHYBA: V .env súbore chýba XTB_USER_ID alebo XTB_PASSWORD.\n"
            "       Doplň prosím oba údaje (číslo demo účtu a heslo) a skús znova."
        )
        return 1

    ws = None
    logged_in = False
    try:
        # 1) Pripojenie na WebSocket
        try:
            ws = create_connection(XTB_DEMO_URL, timeout=CONNECT_TIMEOUT)
        except (WebSocketException, OSError) as exc:
            print(f"CHYBA: Nepodarilo sa pripojiť na {XTB_DEMO_URL}: {exc}")
            return 1

        print(f"Pripojené na {XTB_DEMO_URL}")

        # 2) Prihlásenie
        login_response = send_command(
            ws,
            "login",
            {"userId": user_id, "password": password},
        )

        if not login_response.get("status"):
            code = login_response.get("errorCode", "N/A")
            desc = login_response.get("errorDescr", "neznáma chyba")
            print(f"CHYBA: Prihlásenie zlyhalo (kód {code}): {desc}")
            print("       Skontroluj správnosť XTB_USER_ID a XTB_PASSWORD v .env.")
            return 1

        logged_in = True
        print("Prihlásenie úspešné.")

        # 3) Načítanie symbolu EURUSD
        symbol_response = send_command(ws, "getSymbol", {"symbol": "EURUSD"})

        if not symbol_response.get("status"):
            code = symbol_response.get("errorCode", "N/A")
            desc = symbol_response.get("errorDescr", "neznáma chyba")
            print(f"CHYBA: Nepodarilo sa načítať symbol EURUSD (kód {code}): {desc}")
            return 1

        data = symbol_response.get("returnData", {})
        bid = data.get("bid")
        ask = data.get("ask")

        if bid is None or ask is None:
            print("CHYBA: Odpoveď servera neobsahuje bid/ask pre EURUSD.")
            return 1

        spread = round(ask - bid, 5)
        print("\n=== EURUSD ===")
        print(f"  Bid:    {bid}")
        print(f"  Ask:    {ask}")
        print(f"  Spread: {spread}")

        return 0

    except (WebSocketException, OSError) as exc:
        print(f"CHYBA: Zlyhala komunikácia so serverom: {exc}")
        return 1
    except json.JSONDecodeError as exc:
        print(f"CHYBA: Server vrátil neplatnú (nečitateľnú) odpoveď: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nPrerušené používateľom.")
        return 130
    finally:
        # 4) Korektné odhlásenie a uzavretie spojenia
        if ws is not None:
            try:
                if logged_in:
                    send_command(ws, "logout")
                    print("\nOdhlásené.")
            except (WebSocketException, OSError, json.JSONDecodeError) as exc:
                print(f"UPOZORNENIE: Odhlásenie sa nepodarilo dokončiť: {exc}")
            finally:
                ws.close()
                print("Spojenie uzavreté.")


if __name__ == "__main__":
    sys.exit(main())
