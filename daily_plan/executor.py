#!/usr/bin/env python3
"""Executor (§1 krok 6) — mechanické vykonanie zmrazeného plánu.

Executor plán NEMENÍ. Jediná výnimka je news guard (§4.4). Všetko, čo sa
odchýli od plánu, ide do denníka odchýlok.

Zdieľaný účet s gridom: Daily Plan sa dotýka VÝHRADNE pozícií s vlastným
labelom. Limity z §4 sa počítajú z virtuálnej equity (§journal), aby ich
nespúšťal grid. Naviac však strážime skutočnú voľnú maržu účtu — pri
zdieľanom účte môže grid vyčerpať maržu bez toho, aby o tom virtuálna
equity vedela, a to je jediná diera, ktorú tá voľba má.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daily_plan.journal import Journal

log = logging.getLogger("daily_plan.executor")

LABEL = "DP"                       # prefix labelu — oddeľuje od gridu
PIP = 0.0001

DAILY_STOP_PCT = 1.5
MAX_DD_PCT = 15.0
TIME_STOP_DAYS = 3
FRIDAY_FLAT_UTC = 19               # §4
NO_POSITION_MIN_FREE_MARGIN = 3.0  # násobok požiadavky, §4 margin guard


def label_for(plan_date: str, tag: str) -> str:
    return f"{LABEL}|{plan_date}|{tag}"


def is_ours(lbl: str) -> bool:
    return bool(lbl) and lbl.startswith(LABEL + "|")


class Executor:
    def __init__(self, broker, journal: Journal, tg=None, dry_run: bool = True):
        self.broker = broker
        self.j = journal
        self.tg = tg
        self.dry_run = dry_run
        self.halted_reason: str | None = None
        self._pos_cache: list[dict] | None = None

    # --- pomocné ----------------------------------------------------------
    def _positions(self) -> list[dict]:
        """Pozície účtu, cachované na jeden tick — bez cache išli na
        brokera 3+ reconcile requesty za beh."""
        if self._pos_cache is None:
            self._pos_cache = self.broker.positions()
        return self._pos_cache

    def our_positions(self) -> list[dict]:
        return [p for p in self._positions() if is_ours(p.get("label", ""))]

    def floating(self, price: float) -> float:
        f = 0.0
        for p in self.our_positions():
            d = (price - p["price"]) if p["side"] == "long" else (p["price"] - price)
            f += d * p["units"] + p.get("swap", 0.0)
        return f

    def notify(self, msg: str) -> None:
        log.info(msg)
        if self.tg:
            self.tg.send(f"[DAILY PLAN] {msg}")

    # --- kill switche (§4) ------------------------------------------------
    def check_limits(self, price: float, balance: float,
                     free_margin: float) -> str | None:
        snap = self.j.snapshot(self.floating(price), balance, free_margin)

        if snap["drawdown_pct"] >= MAX_DD_PCT:
            return (f"max drawdown {snap['drawdown_pct']:.1f} % ≥ {MAX_DD_PCT} % "
                    f"— plný stop, vyžaduje manuálny reštart")

        start = float(self.j.get_meta("start_equity", 5000.0))
        if self.j.pnl_today() <= -start * DAILY_STOP_PCT / 100:
            return (f"denná strata {self.j.pnl_today():.2f} € prekročila "
                    f"{DAILY_STOP_PCT} % — žiadne nové vstupy dnes")
        return None

    # --- vstup ------------------------------------------------------------
    T2_GUARD_MIN = 30

    def _t2_blocked(self, plan: dict) -> str | None:
        """±30 min okolo T2 udalosti sa nevstupuje (§L4). T1 blokuje celý
        deň už scenario engine cez A2; T2 chránime tu, lebo závisí od
        aktuálneho času, nie od plánu."""
        now = datetime.now(timezone.utc)
        for e in (plan.get("context") or {}).get("L4_events", []):
            if e.get("tier") != 2:
                continue
            try:
                ts = datetime.fromisoformat(e["ts"])
            except (KeyError, ValueError):
                continue
            if abs((now - ts).total_seconds()) <= self.T2_GUARD_MIN * 60:
                return f"{ts:%H:%M} {e.get('title', '?')}"
        return None

    def try_enter(self, plan: dict, scn: dict, price: float,
                  h1_closed: float | None) -> bool:
        pd_ = plan["plan_date"]

        blocked = self._t2_blocked(plan)
        if blocked:
            if not self._trigger_hit(scn, price, h1_closed):
                return False
            self.j.deviation("t2_guard",
                             f"trigger {scn['tag']} padol v T2 okne ({blocked}) "
                             f"— vstup vynechaný", pd_)
            self.notify(f"vstup {scn['tag']} vynechaný — T2 okno ({blocked})")
            return False

        if self.j.traded_today(pd_) >= 1:
            return False                       # §4: max 1 vstup denne
        if self.our_positions():
            return False                       # §4: max 1 otvorená pozícia
        if not self._trigger_hit(scn, price, h1_closed):
            return False

        # margin guard §4 — na zdieľanom účte je to jediná ochrana proti
        # tomu, aby grid vyčerpal maržu a Daily Plan to nezbadal
        need = scn["volume"] * price / 30.0    # 1:30
        acct = self.broker.account_summary()
        used = sum(p.get("used_margin", 0.0) for p in self._positions())
        free = acct["balance"] - used
        if free < need * NO_POSITION_MIN_FREE_MARGIN:
            self.j.deviation("margin_guard",
                             f"voľná marža {free:.2f} € < {need*3:.2f} € "
                             f"(3× požiadavka) — vstup {scn['tag']} vynechaný", pd_)
            self.notify(f"vstup {scn['tag']} vynechaný — málo voľnej marže "
                        f"({free:.0f} € proti potrebným {need*3:.0f} €)")
            return False

        units = scn["volume"] if scn["side"] == "buy" else -scn["volume"]
        if self.dry_run:
            self.notify(f"[dry-run] vstup {scn['tag']} {scn['side']} "
                        f"{scn['volume']:.0f} @ {price:.5f}, SL {scn['sl']:.5f}")
            return True

        res = self.broker.market_order_with_tp(
            units, scn["tp1"], tag=label_for(pd_, scn["tag"]),
            sl_price=scn["sl"])

        entry = res["price"]
        planned = (scn["entry_lo"] + scn["entry_hi"]) / 2
        self.j.open_trade(
            plan_date=pd_, scenario=scn["tag"], side=scn["side"],
            broker_position_id=res["position_id"],
            label=label_for(pd_, scn["tag"]),
            planned_entry=planned, planned_sl=scn["sl"],
            planned_tp1=scn["tp1"], planned_tp2=scn["tp2"],
            planned_volume=scn["volume"],
            ts_open=time.time(), actual_entry=entry, actual_volume=scn["volume"],
            slippage_entry_pips=abs(entry - planned) / PIP)
        self.notify(f"{scn['tag']} {scn['side'].upper()} {scn['volume']:.0f} "
                    f"@ {entry:.5f} | SL {scn['sl']:.5f} TP1 {scn['tp1']:.5f} "
                    f"(sklz {abs(entry-planned)/PIP:.1f} p)")
        return True

    @staticmethod
    def _trigger_hit(scn: dict, price: float, h1_closed: float | None) -> bool:
        lo, hi, kind, side = (scn["entry_lo"], scn["entry_hi"],
                              scn["kind"], scn["side"])
        if kind == "pullback":
            return lo <= price <= hi
        if kind == "breakout":
            if h1_closed is None:
                return False
            return h1_closed > hi if side == "buy" else h1_closed < lo
        if kind == "rejection":
            if h1_closed is None:
                return False
            return lo <= h1_closed <= hi
        return False

    # --- správa otvorenej pozície ----------------------------------------
    def manage(self, plan: dict, price: float) -> None:
        now = datetime.now(timezone.utc)
        for row in self.j.open_positions():
            pos = next((p for p in self.our_positions()
                        if p["position_id"] == row["broker_position_id"]), None)
            if pos is None:
                self._finalize(row, price, "zatvorené mimo executora")
                continue

            opened = datetime.fromtimestamp(row["ts_open"], timezone.utc)
            age_days = self._business_days(opened, now)

            if age_days >= TIME_STOP_DAYS:
                self._close(row, pos, price, "time_stop")
            elif now.weekday() == 4 and now.hour >= FRIDAY_FLAT_UTC:
                self._close(row, pos, price, "friday")

    @staticmethod
    def _business_days(a: datetime, b: datetime) -> int:
        n, cur = 0, a.date()
        while cur < b.date():
            cur += timedelta(days=1)
            if cur.weekday() < 5:
                n += 1
        return n

    def news_guard(self, reason: str, price: float) -> None:
        """Jediný povolený zásah do zmrazeného plánu (§4.4)."""
        for row in self.j.open_positions():
            pos = next((p for p in self.our_positions()
                        if p["position_id"] == row["broker_position_id"]), None)
            if pos:
                self._close(row, pos, price, "news_guard")
        self.j.deviation("news_guard", reason)
        self.notify(f"news guard: {reason} — všetko zatvorené")

    def _close(self, row, pos: dict, price: float, why: str) -> None:
        if not self.dry_run:
            self.broker.close_position(pos["position_id"])
        self._finalize(row, price, why)

    def _finalize(self, row, price: float, why: str) -> None:
        planned_risk = abs(row["planned_entry"] - row["planned_sl"])
        entry = row["actual_entry"] or row["planned_entry"]
        vol = row["actual_volume"] or row["planned_volume"]
        sign = 1 if row["side"] == "buy" else -1
        # Reálne čísla zo zatváracieho dealu, ak sa dajú získať — denník
        # rozhoduje o osude systému (§6), odhad z aktuálnej ceny by ho
        # systematicky skresľoval pri TP/SL zavretých serverom.
        close_price, comm, swap = price, vol * 0.90 / 10_000, 0.0
        try:
            deals = self.broker.closed_deals_since(int(row["ts_open"] * 1000))
            d = deals.get(row["broker_position_id"])
            if d:
                close_price = d["close_price"] or price
                comm = d["commission"]
                swap = d["swap"]
        except Exception:  # noqa: BLE001 — fallback na odhad
            pass
        gross = sign * (close_price - entry) * vol
        net = gross - comm + swap
        price = close_price
        r = net / (planned_risk * vol) if planned_risk and vol else None
        self.j.close_trade(row["id"], ts_close=time.time(), actual_exit=price,
                           exit_reason=why, gross_eur=gross, commission_eur=comm,
                           net_eur=net, r_multiple=r)
        self.notify(f"{row['scenario']} zatvorené ({why}) @ {price:.5f} | "
                    f"net {net:+.2f} € = {r:+.2f} R" if r is not None else
                    f"{row['scenario']} zatvorené ({why})")

    # --- jeden cyklus -----------------------------------------------------
    @staticmethod
    def plan_approved(plan: dict) -> bool:
        """Bez výslovného schválenia človekom sa nevstupuje.

        Toto je jediná brána medzi plánom a realitou. Plán so statusom
        pending, rejected ani expired sa nevykonáva — a keďže status je
        jediné, čo sa na pláne dá meniť (§5 + trigger v DB), nedá sa to
        obísť dodatočnou úpravou scenárov.
        """
        return plan.get("status") == "approved"

    def tick(self, plan: dict, price: float, h1_closed: float | None = None
             ) -> None:
        if self.halted_reason:
            return
        self._pos_cache = None            # čerstvý pohľad raz za tick
        acct = self.broker.account_summary()
        used = sum(p.get("used_margin", 0.0) for p in self._positions())
        halt = self.check_limits(price, acct["balance"], acct["balance"] - used)

        self.manage(plan, price)

        if halt:
            if "drawdown" in halt:
                self.halted_reason = halt
            self.j.deviation("kill_switch", halt, plan["plan_date"])
            self.notify(f"🛑 {halt}")
            return

        if "WARNING_stale_data" in plan or plan.get("stale_warning"):
            self.j.deviation("stale_plan",
                             plan.get("WARNING_stale_data")
                             or plan.get("stale_warning"), plan["plan_date"])
            return

        if not self.plan_approved(plan):
            return          # ticho — čakanie na schválenie nie je odchýlka

        for scn in plan["scenarios"]:
            if scn["tag"] == "A2" or scn["side"] is None:
                continue
            if self.try_enter(plan, scn, price, h1_closed):
                break              # P a A1 sú OCO
