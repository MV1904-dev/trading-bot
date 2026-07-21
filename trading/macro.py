"""Makro filter — ekonomický kalendár ForexFactory (voľný JSON feed).

Zdroj: https://nfs.faireconomy.media/ff_calendar_thisweek.json (oficiálny
feed ForexFactory, bez auth). Cache 12 h v data/ff_calendar.json.

Blackout: 30 min pred a po high-impact udalostiach v USD alebo EUR sa
neotvárajú nové pozície (existujúce TP bežia ďalej).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CACHE_TTL = 12 * 3600
BLACKOUT_S = 30 * 60
CURRENCIES = {"USD", "EUR"}


class MacroCalendar:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.events: list[dict] = []      # [{ts, currency, title, impact}]
        self._fetched = 0.0

    def refresh(self) -> None:
        """Načíta feed (z cache, ak je čerstvá)."""
        now = time.time()
        if self.events and now - self._fetched < CACHE_TTL:
            return
        raw = None
        if self.cache_path.exists() and now - self.cache_path.stat().st_mtime < CACHE_TTL:
            raw = self.cache_path.read_text()
        else:
            try:
                req = urllib.request.Request(FEED_URL,
                                             headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw = r.read().decode()
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(raw)
            except Exception as exc:  # noqa: BLE001
                log.warning("Kalendár sa nepodarilo stiahnuť: %s", exc)
                if self.cache_path.exists():
                    raw = self.cache_path.read_text()   # stará cache je lepšia než nič
        if not raw:
            return
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("Kalendár: neplatný JSON: %s", exc)
            return
        events = []
        for it in items:
            if (it.get("impact") or "").lower() != "high":
                continue
            if (it.get("country") or "").upper() not in CURRENCIES:
                continue
            try:
                ts = datetime.fromisoformat(it["date"]).timestamp()
            except (KeyError, ValueError):
                continue
            events.append({"ts": ts, "currency": it["country"].upper(),
                           "title": it.get("title", "?"),
                           "impact": "high"})
        self.events = sorted(events, key=lambda e: e["ts"])
        self._fetched = now
        log.info("Kalendár: %d high-impact USD/EUR udalostí tento týždeň.",
                 len(self.events))

    def active_blackout(self, now: Optional[float] = None) -> Optional[dict]:
        """Vráti udalosť, ktorej blackout okno (±30 min) práve beží."""
        now = now or time.time()
        for e in self.events:
            if abs(now - e["ts"]) <= BLACKOUT_S:
                return e
        return None

    def todays_events(self, tz) -> list[dict]:
        """Dnešné high-impact udalosti (v lokálnej časovej zóne tz)."""
        today = datetime.now(tz).date()
        out = []
        for e in self.events:
            if datetime.fromtimestamp(e["ts"], tz).date() == today:
                out.append(e)
        return out
