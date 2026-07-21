"""Telegram vrstva bota — sendMessage + getUpdates cez HTTPS API (stdlib).

Kredenciály z .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. Ak chýbajú,
vrstva sa správa ako no-op a len loguje (bot beží ďalej bez notifikácií).
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Callable, Optional

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token or ""
        self.chat_id = str(chat_id or "")
        self.offset = 0
        if not self.enabled:
            log.warning("Telegram nie je nakonfigurovaný "
                        "(TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID v .env).")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _api(self, method: str, params: dict, timeout: float = 15) -> Optional[dict]:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = urllib.parse.urlencode(params).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as exc:  # noqa: BLE001 — TG nesmie zhodiť bota
            log.warning("Telegram %s zlyhal: %s", method, exc)
            return None

    def send(self, text: str, silent: bool = False) -> None:
        if not self.enabled:
            log.info("[TG mimo prevádzky] %s", text.replace("\n", " | "))
            return
        self._api("sendMessage", {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": "true" if silent else "false",
        })

    def poll_commands(self, handler: Callable[[str, str], None]) -> None:
        """Krátky poll getUpdates; handler(command, args) pre každý príkaz.

        Reaguje len na správy zo zadaného chat_id (ochrana pred cudzími).
        """
        if not self.enabled:
            return
        resp = self._api("getUpdates",
                         {"offset": self.offset + 1, "timeout": 0}, timeout=10)
        if not resp or not resp.get("ok"):
            return
        for upd in resp.get("result", []):
            self.offset = max(self.offset, upd["update_id"])
            msg = upd.get("message") or upd.get("edited_message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            text = (msg.get("text") or "").strip()
            if chat != self.chat_id or not text.startswith("/"):
                continue
            parts = text.split(maxsplit=1)
            cmd = parts[0].split("@")[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            try:
                handler(cmd, args)
            except Exception as exc:  # noqa: BLE001
                log.exception("Chyba pri spracovaní príkazu %s", cmd)
                self.send(f"⚠️ Chyba príkazu {cmd}: {exc}")
