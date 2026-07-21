"""Tenký transport pre XTB xAPI cez priamy TLS socket (xapia/xapib.x-station.eu).

Po vypnutí verejných WebSocket hostov (ws.xtb.com – 14. 3. 2025) a keďže proxy
ws.xapi.pro má zakázané obchodovanie ("XApi trading disabled"), používame pôvodné
priame endpointy XTB na porte 5124 (DEMO) / 5112 (REAL).

Objekt XtbSocket zámerne kopíruje rozhranie websocket-client (.send(str) /
.recv() -> str / .close()), aby existujúce skripty fungovali bez väčších zmien.

Protokol: príkaz sa posiela ako JSON; odpoveď je JSON ukončený dvojicou '\\n\\n'.
"""

from __future__ import annotations

import socket
import ssl

# DEMO hosty (dva zameniteľné) a porty
DEMO_HOSTS = ["xapia.x-station.eu", "xapib.x-station.eu"]
DEMO_PORT = 5124
REAL_PORT = 5112
MSG_END = b"\n\n"


class XtbSocket:
    """Websocket-podobný wrapper nad TLS socketom voči XTB xAPI."""

    def __init__(self, sock: ssl.SSLSocket, timeout: float):
        self._sock = sock
        self._sock.settimeout(timeout)
        self._buf = b""

    def send(self, data) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._sock.sendall(data)

    def recv(self) -> str:
        """Vráti jednu kompletnú JSON správu (bez koncového '\\n\\n')."""
        while MSG_END not in self._buf:
            chunk = self._sock.recv(8192)
            if not chunk:
                raise ConnectionError("XTB socket bol uzavretý serverom.")
            self._buf += chunk
        msg, self._buf = self._buf.split(MSG_END, 1)
        return msg.decode("utf-8").strip()

    def ping(self) -> None:
        """Udrží spojenie počas dlhšej nečinnosti (XTB inak socket zavrie)."""
        self.send('{"command":"ping"}')

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def create_connection_demo(timeout: float = 20, hosts=None) -> XtbSocket:
    """Pripojí sa na prvý dostupný DEMO endpoint a vráti XtbSocket."""
    ctx = ssl.create_default_context()
    last_exc: Exception | None = None
    for host in (hosts or DEMO_HOSTS):
        try:
            raw = socket.create_connection((host, DEMO_PORT), timeout=timeout)
            sock = ctx.wrap_socket(raw, server_hostname=host)
            return XtbSocket(sock, timeout)
        except OSError as exc:  # timeout, DNS, refused …
            last_exc = exc
            continue
    raise ConnectionError(
        f"Nepodarilo sa pripojiť na žiadny XTB DEMO endpoint {hosts or DEMO_HOSTS}: {last_exc}"
    )
