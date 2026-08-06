"""Push stavu bota do Supabase + príkazy z dashboardu späť.

Zdroj pravdy ostáva SQLite (`data/bot_ctrader.db`) a broker. Supabase je len
zrkadlo pre čítanie + fronta príkazov; keby bol nedostupný, bot obchoduje
ďalej a rozdiel sa dorovná pri najbližšom úspešnom pushi.

Prečo stdlib a nie `supabase-py`: rovnaký dôvod ako pri trading/tg.py —
bot beží na serveri s minimom závislostí a PostgREST je obyčajné HTTP.

Vlákna:
  * push        — každých PUSH_EVERY_S, plus okamžite po `trigger()`
  * commands    — každých POLL_EVERY_S číta `commands` so status='pending'

Exekučná moc ostáva v obchodnom vlákne: poller iba zaradí príkaz do fronty,
ktorú vyprázdni bot v `_tick`. Výnimka je pause/start — mení jediný float,
takže sa vybaví hneď a funguje aj keď obchodná slučka viazne (rovnaká úvaha
ako pri Telegram príkazoch).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

PUSH_EVERY_S = 60.0
POLL_EVERY_S = 5.0
EQUITY_EVERY_S = 300.0      # snapshot equity — hustejšie nemá informačnú cenu
HTTP_TIMEOUT = 15.0


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class SupabaseSync:
    def __init__(self, url: str = "", key: str = "", bot_id: str = "ctrader",
                 env: str = "demo"):
        self.url = (url or os.getenv("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.getenv("SUPABASE_SERVICE_KEY", "")
        self.bot_id = bot_id
        # 'demo' | 'live' — rozmer v zrkadlových tabuľkách; id dealov sú
        # per-server, bez env by sa demo a live história ticho pomiešali.
        self.env = env
        self.enabled = bool(self.url and self.key)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._cmd_queue: queue.Queue = queue.Queue()
        self._last_equity = 0.0
        self._fail_streak = 0
        self._last_ok = False
        # Posledný ts_close, ktorý Supabase POTVRDILA. Obchodné vlákno
        # si ho odtiaľto prevezme a až potom posunie značku v SQLite —
        # keby sa posúvala pri stavaní snapshotu, výpadok siete by
        # históriu ticho preskočil.
        self.confirmed_trades_until: float = 0.0
        if not self.enabled:
            log.info("Supabase sync vypnutý (chýba SUPABASE_URL/SERVICE_KEY).")

    # --- HTTP -------------------------------------------------------------
    def _req(self, method: str, path: str, body: Any = None,
             prefer: str = "") -> Optional[Any]:
        if not self.enabled:
            return None
        url = f"{self.url}/rest/v1/{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "apikey": self.key,
            "Content-Type": "application/json",
        }
        # Nové sb_secret_ kľúče nie sú JWT a na hlavičke Authorization ich
        # časť Supabase odmieta — patria výhradne do `apikey`. Legacy
        # service_role JWT naopak Authorization očakáva, takže ju pridáme
        # len preň.
        if self.key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {self.key}"
        if prefer:
            headers["Prefer"] = prefer
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        self._last_ok = False
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                raw = r.read()
                self._fail_streak = 0
                self._last_ok = True
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            log.warning("Supabase %s %s → %s: %s", method, path, exc.code,
                        detail)
        except Exception as exc:  # noqa: BLE001 — sync nesmie zhodiť bota
            self._fail_streak += 1
            # Prvý výpadok zalogujeme, ďalšie už len po desiatkach, aby
            # dlhší výpadok siete nezaplavil log.
            if self._fail_streak == 1 or self._fail_streak % 20 == 0:
                log.warning("Supabase %s %s zlyhal (%d. raz): %s",
                            method, path, self._fail_streak, exc)
        return None

    def _upsert(self, table: str, rows: list[dict],
                on_conflict: str = "") -> bool:
        """True = zapísané (alebo nebolo čo zapisovať)."""
        if not rows:
            return True
        path = table
        if on_conflict:
            path += f"?on_conflict={on_conflict}"
        # _req vracia None aj pri úspechu (return=minimal má prázdne telo),
        # preto sa úspech číta z explicitného príznaku, nie z návratovej
        # hodnoty.
        self._req("POST", path, rows,
                  prefer="resolution=merge-duplicates,return=minimal")
        return self._last_ok

    # --- verejné API ------------------------------------------------------
    def trigger(self) -> None:
        """Vyžiada okamžitý push (volať pri otvorení/zavretí obchodu)."""
        self._wake.set()

    def start(self, snapshot: Callable[[], dict],
              on_command: Callable[[dict], None],
              on_pause: Callable[[str, dict], None]) -> None:
        """snapshot() → dict s dátami na push (volá sa v push vlákne, musí
        byť bezpečné na čítanie); on_command(cmd) zaradí príkaz do obchodného
        vlákna; on_pause(action, cmd) vybaví pause/start hneď."""
        if not self.enabled:
            return
        self._snapshot = snapshot
        self._on_command = on_command
        self._on_pause = on_pause
        threading.Thread(target=self._push_loop, name="sb-push",
                         daemon=True).start()
        threading.Thread(target=self._cmd_loop, name="sb-commands",
                         daemon=True).start()
        log.info("Supabase sync beží (push %.0fs, príkazy %.0fs).",
                 PUSH_EVERY_S, POLL_EVERY_S)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # --- push -------------------------------------------------------------
    def _push_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.push(self._snapshot())
            except Exception:  # noqa: BLE001 — vlákno musí prežiť všetko
                log.exception("Chyba v Supabase push vlákne")
            self._wake.wait(PUSH_EVERY_S)
            self._wake.clear()

    def push(self, snap: dict) -> None:
        """snap: {'state','positions','trades','daily','account'}"""
        if not self.enabled:
            return
        state = dict(snap.get("state") or {})
        state["id"] = self.bot_id
        state["env"] = self.env
        state["heartbeat_at"] = _iso(time.time())
        state["updated_at"] = _iso(time.time())
        self._upsert("bot_state", [state], on_conflict="env,id")

        positions = [dict(p, env=self.env) for p in snap.get("positions") or []]
        self._upsert("positions", positions, on_conflict="env,id")
        # Zavreté pozície musia zo zrkadla zmiznúť, inak by v dashboarde
        # viseli navždy. Mažeme všetko, čo nie je v aktuálnom zozname —
        # ale len vo vlastnom env, inak by live bot zmazal demo históriu.
        ids = [str(p["id"]) for p in positions]
        if ids:
            self._req("DELETE",
                      f"positions?env=eq.{self.env}&id=not.in.({','.join(ids)})")
        else:
            self._req("DELETE", f"positions?env=eq.{self.env}&id=gte.0")

        trades = [dict(t, env=self.env) for t in snap.get("trades") or []]
        if self._upsert("trades", trades, on_conflict="env,id"):
            self.confirmed_trades_until = max(
                self.confirmed_trades_until,
                float(snap.get("trades_until") or 0.0))
        daily = [dict(d, env=self.env) for d in snap.get("daily") or []]
        self._upsert("daily_cycles", daily, on_conflict="env,day,strategy")

        acct = snap.get("account") or {}
        now = time.time()
        if acct and now - self._last_equity >= EQUITY_EVERY_S:
            self._last_equity = now
            self._req("POST", "equity_snapshots?on_conflict=env,ts", [{
                "env": self.env,
                "ts": _iso(now),
                "balance": acct.get("balance", 0.0),
                "equity": acct.get("equity", 0.0),
                "floating_pnl": acct.get("floating_pnl", 0.0),
                "open_positions": acct.get("open_positions", 0),
            }], prefer="resolution=merge-duplicates,return=minimal")

    def push_calendar(self, rows: list[dict]) -> None:
        """Kalendár udalostí. Kľúč je (ts, currency, title), takže
        opakovaný push tie isté udalosti len prepíše."""
        self._upsert("calendar_events", rows,
                     on_conflict="ts,currency,title")

    # --- príkazy ----------------------------------------------------------
    def _cmd_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_commands()
            except Exception:  # noqa: BLE001
                log.exception("Chyba v Supabase command vlákne")
            self._stop.wait(POLL_EVERY_S)

    def _poll_commands(self) -> None:
        rows = self._req(
            "GET", "commands?status=eq.pending&order=created_at.asc&limit=10")
        if not rows:
            return
        for cmd in rows:
            # Označíme si príkaz ako prevzatý hneď, aby ho druhý cyklus
            # nezobral druhý raz (bot je jediný konzument, ale reštart
            # uprostred by inak príkaz zopakoval).
            if not self._claim(cmd["id"]):
                continue
            action = cmd.get("action")
            log.info("Supabase príkaz %s (%s)", action, cmd["id"])
            try:
                if action in ("pause", "start"):
                    self._on_pause(action, cmd)
                else:
                    self._on_command(cmd)
            except Exception as exc:  # noqa: BLE001
                log.exception("Príkaz %s zlyhal", cmd["id"])
                self.finish(cmd["id"], False, str(exc))

    def _claim(self, cmd_id: str) -> bool:
        res = self._req(
            "PATCH", f"commands?id=eq.{cmd_id}&status=eq.pending",
            {"status": "running", "picked_at": _iso(time.time())},
            prefer="return=representation")
        return bool(res)

    def finish(self, cmd_id: str, ok: bool, result: str = "") -> None:
        self._req("PATCH", f"commands?id=eq.{cmd_id}", {
            "status": "done" if ok else "failed",
            "result": result[:500],
            "executed_at": _iso(time.time()),
        }, prefer="return=minimal")
