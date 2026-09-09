#!/usr/bin/env python3
"""Diagnostický log app authu pre Spotware Open API tím.

Lev si vyžiadal logy s UTC časmi, IP adresou a clientMsgId. Bežný log bota
nemá ani jedno: SDK si clientMsgId generuje samo ako str(id(deferred))
a nikde ho nevypíše (ctrader_open_api/client.py:52), časy sú v lokálnom
čase a IP v logu nie je vôbec.

Skript pošle ProtoOAApplicationAuthReq s VLASTNÝM čitateľným clientMsgId
proti demo aj live hostu a vypíše presnú časovú os v UTC. Výstup je určený
na skopírovanie do tikety alebo do Telegram skupiny.

Navyše dvíha timeout deferredu (SDK má default 5 s). Ak by odpoveď prišla
napríklad po 12 s, doterajšie meranie by ju nikdy nevidelo a tvárilo by sa
ako úplné ticho — to treba vylúčiť skôr, než tvrdíme, že brána neodpovedá.

Použitie:
    ./.venv/bin/python scripts/ctrader_authlog.py            # z .env
    ./.venv/bin/python scripts/ctrader_authlog.py --ask      # iná aplikácia
    ./.venv/bin/python scripts/ctrader_authlog.py --timeout 60
"""

from __future__ import annotations

import argparse
import getpass
import os
import socket
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq)
from trading.broker_ctrader import _ensure_reactor


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _local_ip(host: str, port: int) -> str:
    """Zdrojová IP, ktorú OS použije na spojenie s daným hostom.

    UDP 'connect' nič neposiela, len vyberie trasu — spoľahlivé aj bez
    odchádzajúcej prevádzky."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, port))
        return s.getsockname()[0]
    except OSError as exc:
        return f"(nezistená: {exc})"
    finally:
        s.close()


def probe(label: str, host: str, cid: str, secret: str,
          timeout: float) -> None:
    port = EndPoints.PROTOBUF_PORT
    msg_id = f"diag-{label.lower()}-{uuid.uuid4().hex[:8]}"
    lines: list[str] = []
    done = threading.Event()

    def log(text: str) -> None:
        lines.append(f"{_utc()}  {text}")

    try:
        server_ip = socket.gethostbyname(host)
    except OSError as exc:
        server_ip = f"(DNS zlyhalo: {exc})"

    print(f"\n=== {label}  {host}:{port} ===")
    print(f"server IP       : {server_ip}")
    print(f"local source IP : {_local_ip(host, port)}")
    print(f"clientMsgId     : {msg_id}")
    print(f"deferred timeout: {timeout:.0f} s")

    client = Client(host, port, TcpProtocol)

    def on_response(message) -> None:
        try:
            payload = Protobuf.extract(message)
            name = type(payload).__name__
            detail = ""
            if name == "ProtoOAErrorRes":
                detail = (f" errorCode={getattr(payload, 'errorCode', '?')} "
                          f"description="
                          f"{getattr(payload, 'description', '')!r}")
        except Exception as exc:  # noqa: BLE001 — diagnostika
            name, detail = "(neznáma správa)", f" ({exc})"
        log(f"ODPOVEĎ: {name}{detail}")
        done.set()

    def on_failure(failure) -> None:
        log(f"ZLYHANIE: {failure.type.__name__}: "
            f"{failure.getErrorMessage() or '(bez textu)'}")
        done.set()

    def on_connected(_client) -> None:
        log("TCP spojenie nadviazané")
        req = ProtoOAApplicationAuthReq()
        req.clientId = cid
        req.clientSecret = secret
        log(f"ODOSLANÉ: ProtoOAApplicationAuthReq clientMsgId={msg_id}")
        d = client.send(req, clientMsgId=msg_id,
                        responseTimeoutInSeconds=timeout)
        d.addCallbacks(on_response, on_failure)

    def on_disconnected(_client, reason) -> None:
        log(f"ODPOJENÉ: {reason.getErrorMessage() or reason}")

    client.setConnectedCallback(on_connected)
    client.setDisconnectedCallback(on_disconnected)

    from twisted.internet import reactor
    _ensure_reactor()
    reactor.callFromThread(client.startService)
    # +10 s rezerva, aby sa stihol zapísať aj timeout a následné odpojenie
    done.wait(timeout + 10)
    reactor.callFromThread(client.stopService)
    done.wait(1.0)

    for line in lines:
        print(f"  {line}")
    if not lines:
        print("  (žiadna udalosť — spojenie sa ani nenadviazalo)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ask", action="store_true",
                    help="spýtať sa na credentials inej aplikácie")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="timeout deferredu v sekundách (SDK má default 5)")
    args = ap.parse_args()

    load_dotenv()
    if args.ask:
        cid = input("Client ID: ").strip()
        secret = getpass.getpass("Client Secret (nezobrazí sa): ").strip()
    else:
        cid = os.getenv("CTRADER_CLIENT_ID", "")
        secret = os.getenv("CTRADER_CLIENT_SECRET", "")
    if not cid or not secret:
        print("CHYBA: chýba CLIENT_ID / CLIENT_SECRET.", file=sys.stderr)
        return 2

    print("cTrader Open API — app auth diagnostika")
    print(f"čas spustenia (UTC): {_utc()}")
    print(f"clientId (koniec)  : …{cid[-6:]}")
    probe("DEMO", EndPoints.PROTOBUF_DEMO_HOST, cid, secret, args.timeout)
    probe("LIVE", EndPoints.PROTOBUF_LIVE_HOST, cid, secret, args.timeout)
    print("\n(časy sú v UTC; clientMsgId je zadaný explicitne, "
          "nie generovaný SDK)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
