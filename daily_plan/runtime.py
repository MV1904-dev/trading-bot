#!/usr/bin/env python3
"""Runtime Daily Plan executora vnútri bota.

Spotware demo dovolí jedno app-auth spojenie a drží ho bot — executor
preto beží v jeho procese, rovnako ako Supabase sync a shadow judge.
Bot ho volá z _tick; všetko je obalené tak, aby chyba Daily Planu nikdy
nezhodila grid.

Živé ordery sú default. DAILY_PLAN_DRY=1 v .env prepne späť na suchý beh.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from daily_plan.executor import Executor
from daily_plan.journal import Journal

log = logging.getLogger("daily_plan.runtime")

RUN_EVERY_S = 30.0        # limity, správa pozícií a triggery
PLAN_REFRESH_S = 60.0     # ako často sa pýtať Supabase na schválený plán


class DailyPlanRunner:
    def __init__(self, broker, sync, tg, journal_path=None):
        self.sync = sync
        self.journal = Journal(**({"path": journal_path} if journal_path else {}))
        dry = os.getenv("DAILY_PLAN_DRY", "0") == "1"
        self.ex = Executor(broker, self.journal, tg=tg, dry_run=dry)
        self.enabled = sync.enabled
        self._plan: dict | None = None
        self._plan_ts = 0.0
        self._last_run = 0.0
        # vlastný H1 agregátor — bot skladá bary len pre TF stratégií (M5)
        self._h1_bucket: int | None = None
        self._h1_close_val: float | None = None
        self._h1_last_closed: float | None = None
        if not self.enabled:
            log.info("Daily Plan runner vypnutý (bez Supabase).")
        else:
            log.info("Daily Plan runner aktívny (%s).",
                     "DRY RUN" if dry else "ŽIVÉ ORDERY")

    # --- H1 close z tikov -------------------------------------------------
    def feed_price(self, mid: float) -> None:
        bucket = int(time.time() // 3600)
        if self._h1_bucket is None:
            self._h1_bucket = bucket
        elif bucket != self._h1_bucket:
            self._h1_last_closed = self._h1_close_val
            self._h1_bucket = bucket
        self._h1_close_val = mid

    # --- plán -------------------------------------------------------------
    def _today(self) -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _fetch_plan(self) -> None:
        if time.time() - self._plan_ts < PLAN_REFRESH_S:
            return
        self._plan_ts = time.time()
        rows = self.sync._req(
            "GET", f"daily_plans?plan_date=eq.{self._today()}"
                   f"&status=eq.approved&select=*")
        new = rows[0] if rows else None
        if new and (self._plan is None
                    or self._plan.get("plan_date") != new["plan_date"]):
            log.info("Schválený plán %s načítaný.", new["plan_date"])
            self.ex.notify(f"plán {new['plan_date']} schválený — executor "
                           f"ho preberá")
        self._plan = new

    # --- hlavný vstup z bota ----------------------------------------------
    def tick(self, mid: float | None) -> None:
        if not self.enabled:
            return
        if mid is not None:
            self.feed_price(mid)
        if time.time() - self._last_run < RUN_EVERY_S:
            return
        self._last_run = time.time()
        try:
            self._fetch_plan()
            if self._plan is None or mid is None:
                return
            self.ex.tick(self._plan, mid, h1_closed=self._h1_last_closed)
        except Exception:  # noqa: BLE001 — Daily Plan nesmie zhodiť grid
            log.exception("Chyba v Daily Plan runneri")
