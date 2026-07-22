#!/usr/bin/env python3
"""Druhá inštancia bota — Oanda practice účet, nezávislá od IBKR bota.

Konfigurácia (podľa Oanda vetvy labu):
* stratégia Grid25-G2B (gap → TP na preskočenú úroveň), pozícia 2 000 EUR
* kapacita 20 úrovní na smer BEZ rezervy (G3_cap20)
* G8 režimová poistka: kurz > 2 % nad/pod 3-ročným extrémom → kapacita
  sa zníži na polovicu + Telegram alarm; uvoľní sa s 1 % hysterézou
* všetky Telegram správy majú prefix [OANDA]

Oddelenie od IBKR bota:
* vlastná DB data/bot_oanda.db, vlastný log data/bot_oanda.log
* Telegram DEFAULTNE len odosiela (getUpdates polling by kradol updaty
  IBKR botovi). Ak chceš príkazy aj pre túto inštanciu, vytvor druhého
  bota u @BotFather a daj token do OANDA_TELEGRAM_BOT_TOKEN v .env —
  vtedy sa zapnú aj /stav /pozicie /pauza /start.
* funding sa neúčtuje modelovo — pri zavretí sa preberá SKUTOČNÝ
  financing z Oanda API (pole financing v detaile obchodu).

Bezpečnosť: practice endpoint natvrdo; live vyžaduje zmenu PRACTICE=False
v kóde + env OANDA_CONFIRM_LIVE="ROZUMIEM-RIZIKU".

.env: OANDA_API_TOKEN=..., OANDA_ACCOUNT_ID=101-...-001
Spustenie: python3 bot_oanda.py [--run-minutes N]
"""

from __future__ import annotations

import argparse
import csv as _csv
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from trading.botdb import BotDB
from trading.broker_oanda import OandaBroker, OandaError
from trading.macro import MacroCalendar
from trading.strategy_base import Bar, Signal
from trading.strategy_grid25 import Grid25, Grid25Config
from trading.tg import Telegram

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("bot_oanda")


@dataclass
class OandaBotConfig:
    PRACTICE: bool = True          # natvrdo; live = vedomá zmena + env potvrdenie
    INSTRUMENT: str = "EUR_USD"
    QTY: float = 2_000
    CAP_BASE: int = 20             # G3_cap20 — bez rezervných úrovní
    CAP_RESERVE: int = 0
    FAILSAFE_BAND: float = 0.02    # G8: 2 % za 3-ročným extrémom
    FAILSAFE_RELEASE: float = 0.01 # hystéréza uvoľnenia
    TICK_SECONDS: float = 10.0
    BAR_SECONDS: int = 300
    ATR_PERIOD: int = 14
    DATA_GAP_ALARM_S: int = 300
    DD_ALARM_PCT: float = 10.0
    TIMEZONE: str = "Europe/Bratislava"
    TG_PREFIX: str = "[OANDA] "
    DB_PATH: Path = field(default_factory=lambda: ROOT / "data" / "bot_oanda.db")
    LOG_PATH: Path = field(default_factory=lambda: ROOT / "data" / "bot_oanda.log")
    CALENDAR_CACHE: Path = field(
        default_factory=lambda: ROOT / "data" / "ff_calendar.json")
    IBKR_M5_CSV: Path = field(
        default_factory=lambda: ROOT / "data" / "ibkr_EURUSD_M5.csv")


class PrefixedTelegram(Telegram):
    """Telegram s [OANDA] prefixom na každej správe."""

    def __init__(self, token: str, chat_id: str, prefix: str):
        super().__init__(token, chat_id)
        self.prefix = prefix

    def send(self, text: str, silent: bool = False) -> None:
        super().send(self.prefix + text, silent=silent)


class OandaBot:
    def __init__(self, cfg: OandaBotConfig):
        self.cfg = cfg
        self.db = BotDB(cfg.DB_PATH)
        self.tz = ZoneInfo(cfg.TIMEZONE)

        load_dotenv()
        own_token = os.getenv("OANDA_TELEGRAM_BOT_TOKEN", "")
        self.tg = PrefixedTelegram(
            own_token or os.getenv("TELEGRAM_BOT_TOKEN", ""),
            os.getenv("TELEGRAM_CHAT_ID", ""), cfg.TG_PREFIX)
        self.commands_enabled = bool(own_token)
        if self.commands_enabled:
            self.tg.offset = int(self.db.meta_get("tg_offset", "0") or 0)

        self.broker = OandaBroker(os.getenv("OANDA_API_TOKEN", ""),
                                  os.getenv("OANDA_ACCOUNT_ID", ""),
                                  practice=cfg.PRACTICE,
                                  instrument=cfg.INSTRUMENT)
        strat = Grid25(Grid25Config(qty=cfg.QTY, base_levels=cfg.CAP_BASE,
                                    reserve_levels=cfg.CAP_RESERVE))
        strat.id = "Grid25-G2B-O"          # vlastné ID pre orderRef/DB
        self.strategy = strat
        self.macro = MacroCalendar(cfg.CALENDAR_CACHE)

        # runtime
        self.atr: float | None = None
        self._atr_prev_close: float | None = None
        self._bar_bucket: int | None = None
        self._bar: Bar | None = None
        self.paused_until = 0.0
        self.auto_paused = False
        self.last_md_ts = time.time()
        self._gap_alarmed = False
        self._dd_alarmed = False
        self._acct_cache: tuple = (0.0, {})
        self._last_close_poll = 0.0
        self._snap_day = ""
        # G8 poistka
        self.daily_closes: list[float] = []
        self._daily_day: str = ""
        self.failsafe = False

    # ------------------------------------------------------------------ #
    def _guard_practice(self) -> None:
        if self.cfg.PRACTICE:
            return
        if os.getenv("OANDA_CONFIRM_LIVE") != "ROZUMIEM-RIZIKU":
            raise SystemExit("CHYBA: PRACTICE=False vyžaduje env "
                             "OANDA_CONFIRM_LIVE='ROZUMIEM-RIZIKU'.")

    def start(self, run_minutes: float = 0.0) -> int:
        self._guard_practice()
        acct = self._account(force=True)
        if not acct:
            print("CHYBA: Oanda účet nedostupný — skontroluj OANDA_API_TOKEN "
                  "a OANDA_ACCOUNT_ID v .env.", file=sys.stderr)
            return 1
        log.info("Pripojené na Oanda practice, NAV %.2f %s.",
                 acct["NAV"], acct["currency"])
        self._bootstrap_atr()
        self._load_daily_extremes()
        self._restore_state()
        self.macro.refresh()
        restarted = os.getenv("BOT_RESTARTED") == "1"
        self.tg.send(f"🤖 <b>Oanda bot {'reštartovaný' if restarted else 'spustený'}</b> "
                     f"(practice, {self.cfg.INSTRUMENT})\n"
                     f"{self.strategy.status_line()}\n"
                     f"NAV: {acct['NAV']:,.2f} {acct['currency']} | "
                     f"pozícia {self.cfg.QTY:,.0f}, kapacita "
                     f"{self.cfg.CAP_BASE}/smer + G8 poistka")
        self.db.log_event("info", "oanda bot štart")

        deadline = time.time() + run_minutes * 60 if run_minutes else None
        try:
            while True:
                try:
                    self._tick()
                except Exception:  # noqa: BLE001
                    log.exception("Chyba v ticku")
                    self.db.log_event("warn", "chyba v ticku (pozri log)")
                if deadline and time.time() >= deadline:
                    self.tg.send("🧪 Suchý test dokončený, Oanda bot sa vypína.")
                    break
                time.sleep(self.cfg.TICK_SECONDS)
        except KeyboardInterrupt:
            self.tg.send("🛑 Oanda bot zastavený (Ctrl-C).")
        finally:
            if self.commands_enabled:
                self.db.meta_set("tg_offset", self.tg.offset)
        return 0

    # ------------------------------------------------------------------ #
    def _bootstrap_atr(self) -> None:
        try:
            candles = self.broker.candles_m5(600)
        except OandaError as exc:
            log.warning("ATR bootstrap zlyhal (%s).", exc)
            return
        n = self.cfg.ATR_PERIOD
        if len(candles) <= n:
            return
        trs = []
        for prev, cur in zip(candles, candles[1:]):
            trs.append(max(cur["h"] - cur["l"],
                           abs(cur["h"] - prev["c"]),
                           abs(cur["l"] - prev["c"])))
        atr = sum(trs[:n]) / n
        for tr in trs[n:]:
            atr = atr * (n - 1) / n + tr / n
        self.atr = atr
        self._atr_prev_close = candles[-1]["c"]
        log.info("ATR(%d, M5) bootstrap: %.6f (%d sviečok).", n, atr, len(candles))

    def _load_daily_extremes(self) -> None:
        """3-ročné denné extrémy pre G8 poistku z lokálnej IBKR cache."""
        closes_by_day: dict[str, float] = {}
        try:
            with open(self.cfg.IBKR_M5_CSV, newline="") as f:
                for row in _csv.DictReader(f):
                    closes_by_day[row["date"][:10]] = float(row["close"])
        except OSError:
            log.warning("G8 poistka: chýba %s — extrémy sa naplnia až zo "
                        "živých dát.", self.cfg.IBKR_M5_CSV)
        days = sorted(closes_by_day)[-756:]
        self.daily_closes = [closes_by_day[d] for d in days]
        self._daily_day = days[-1] if days else ""
        if self.daily_closes:
            log.info("G8 poistka: %d denných záverov, 3r pásmo %.5f–%.5f.",
                     len(self.daily_closes), min(self.daily_closes),
                     max(self.daily_closes))

    def _restore_state(self) -> None:
        rows = self.db.open_trades()
        if not rows:
            return
        try:
            open_ids = {t["id"] for t in self.broker.open_trades()}
        except OandaError as exc:
            log.warning("Obnova: open_trades zlyhalo (%s).", exc)
            return
        recovered = closed_offline = 0
        for row in rows:
            oid = str(row["entry_order_id"])
            if oid in open_ids:
                recovered += 1
                continue
            self._finalize_close(row["id"], offline=True)
            closed_offline += 1
        still = self.db.open_trades()
        self.strategy.restore(still)
        note = (f"Obnova stavu: {recovered} pozícií beží, "
                f"{closed_offline} zavretých počas výpadku.")
        log.info(note)
        self.db.log_event("info", note)
        if closed_offline:
            self.tg.send(f"ℹ️ {note}")

    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        px = self._price()
        if px is not None:
            self._update_failsafe_daily(px)
            self._aggregate_bar(px["mid"])
            self._dd_alarm(px["mid"])
        self._poll_closes()
        self._daily_snapshot()
        self.macro.refresh()
        if self.commands_enabled:
            self.tg.poll_commands(self._handle_command)
            self.db.meta_set("tg_offset", self.tg.offset)

    def _price(self) -> dict | None:
        try:
            px = self.broker.price()
        except OandaError as exc:
            log.warning("Cena nedostupná: %s", exc)
            self._maybe_gap_alarm()
            return None
        if not px["tradeable"]:
            self._maybe_gap_alarm()
            return None
        self.last_md_ts = time.time()
        if self._gap_alarmed:
            self._gap_alarmed = False
            self.auto_paused = False
            self.tg.send("✅ Dáta/API znovu dostupné, pauza zrušená.")
        return px

    def _maybe_gap_alarm(self) -> None:
        stale = time.time() - self.last_md_ts
        if stale > self.cfg.DATA_GAP_ALARM_S and not self._gap_alarmed:
            self.auto_paused = True
            self._gap_alarmed = True
            msg = (f"🚨 Oanda API/dáta nedostupné > {int(stale // 60)} min — "
                   f"nové vstupy stoja, TP (GTC) bežia na serveri ďalej.")
            self.tg.send(msg)
            self.db.log_event("alarm", msg)

    def _account(self, force: bool = False) -> dict:
        now = time.time()
        if not force and now - self._acct_cache[0] < 60 and self._acct_cache[1]:
            return self._acct_cache[1]
        try:
            acct = self.broker.account_summary()
        except OandaError as exc:
            log.warning("account_summary zlyhalo: %s", exc)
            return self._acct_cache[1] or {}
        self._acct_cache = (now, acct)
        return acct

    # --- G8 poistka ---------------------------------------------------------
    def _update_failsafe_daily(self, px: dict) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if day != self._daily_day and self._daily_day:
            self.daily_closes.append(px["mid"])
            self.daily_closes = self.daily_closes[-756:]
            self._daily_day = day
        elif not self._daily_day:
            self._daily_day = day

        if len(self.daily_closes) < 250:
            return
        hi, lo = max(self.daily_closes), min(self.daily_closes)
        mid = px["mid"]
        band = self.cfg.FAILSAFE_BAND
        rel = self.cfg.FAILSAFE_RELEASE
        if not self.failsafe and (mid > hi * (1 + band) or mid < lo * (1 - band)):
            self.failsafe = True
            new_cap = max(int(self.cfg.CAP_BASE * 0.5), 1)
            self.strategy.cfg.base_levels = new_cap
            msg = (f"🚨 <b>G8 poistka AKTÍVNA</b>: kurz {mid:.5f} je > "
                   f"{band:.0%} za 3-ročným extrémom ({lo:.5f}–{hi:.5f}). "
                   f"Kapacita znížená na {new_cap}/smer.")
            self.tg.send(msg)
            self.db.log_event("alarm", msg)
        elif self.failsafe and (lo * (1 - rel) < mid < hi * (1 + rel)):
            self.failsafe = False
            self.strategy.cfg.base_levels = self.cfg.CAP_BASE
            self.tg.send(f"✅ G8 poistka uvoľnená (kurz {mid:.5f} späť v pásme), "
                         f"kapacita {self.cfg.CAP_BASE}/smer.")
            self.db.log_event("info", "G8 poistka uvoľnená")

    # --- bar agregácia a exekúcia --------------------------------------------
    def _aggregate_bar(self, mid: float) -> None:
        bucket = int(time.time() // self.cfg.BAR_SECONDS)
        if self._bar_bucket is None:
            self._bar_bucket = bucket
            self._bar = Bar(bucket * self.cfg.BAR_SECONDS, mid, mid, mid, mid)
            return
        if bucket == self._bar_bucket:
            b = self._bar
            b.high = max(b.high, mid)
            b.low = min(b.low, mid)
            b.close = mid
            return
        closed = self._bar
        self._bar_bucket = bucket
        self._bar = Bar(bucket * self.cfg.BAR_SECONDS, mid, mid, mid, mid)
        n = self.cfg.ATR_PERIOD
        pc = self._atr_prev_close if self._atr_prev_close is not None else closed.open
        tr = max(closed.high - closed.low, abs(closed.high - pc),
                 abs(closed.low - pc))
        self.atr = tr if self.atr is None else self.atr * (n - 1) / n + tr / n
        self._atr_prev_close = closed.close
        if self.strategy.enabled:
            for sig in self.strategy.on_bar(closed, self.atr):
                self._execute(sig, closed)

    def _blocked_reason(self) -> str | None:
        if self.auto_paused:
            return "auto-pauza (výpadok dát/API)"
        if time.time() < self.paused_until:
            return "manuálna pauza"
        ev = self.macro.active_blackout()
        if ev:
            t = datetime.fromtimestamp(ev["ts"], self.tz).strftime("%H:%M")
            return f"makro blackout: {ev['currency']} {ev['title']} o {t}"
        return None

    def _execute(self, sig: Signal, bar: Bar) -> None:
        reason = self._blocked_reason()
        if reason:
            self.db.log_signal(sig.strategy_id, sig.side, bar.close,
                               self.atr or 0.0, 0.0, "blocked", reason,
                               sig.context)
            log.info("Signál %s BLOKOVANÝ: %s", sig.side, reason)
            return
        units = sig.qty if sig.side == "long" else -sig.qty
        try:
            res = self.broker.market_order_with_tp(units, sig.tp_price,
                                                   tag=sig.strategy_id)
        except OandaError as exc:
            self.db.log_signal(sig.strategy_id, sig.side, bar.close,
                               self.atr or 0.0, 0.0, "error", str(exc),
                               sig.context)
            log.warning("Vstup zlyhal: %s", exc)
            return
        ctx = dict(sig.context)
        ctx.update({"reason": sig.reason, "bar_close": bar.close,
                    "failsafe": self.failsafe})
        trade_id = self.db.open_trade(
            sig.strategy_id, sig.side, sig.qty, res["price"], sig.tp_price,
            int(res["trade_id"] or 0), 0, res["commission"], ctx)
        self.strategy.on_trade_opened(trade_id, sig.side, res["price"])
        self.db.log_signal(sig.strategy_id, sig.side, bar.close,
                           self.atr or 0.0, 0.0, "executed", sig.reason, ctx)
        self.tg.send(f"📈 <b>{sig.strategy_id}</b> OTVORENÉ {sig.side.upper()} "
                     f"{sig.qty:,.0f} {self.cfg.INSTRUMENT} @ {res['price']:.5f}\n"
                     f"TP {sig.tp_price:.5f} (GTC na serveri) | ATR "
                     f"{self.atr:.5f}\ndôvod: {sig.reason}")
        log.info("OTVORENÉ %s @ %.5f (oanda trade %s, db #%d)",
                 sig.side, res["price"], res["trade_id"], trade_id)

    # --- detekcia zavretí -----------------------------------------------------
    def _poll_closes(self) -> None:
        now = time.time()
        if now - self._last_close_poll < 30:
            return
        self._last_close_poll = now
        rows = self.db.open_trades()
        if not rows:
            return
        try:
            open_ids = {t["id"] for t in self.broker.open_trades()}
        except OandaError:
            return
        for row in rows:
            if str(row["entry_order_id"]) not in open_ids:
                self._finalize_close(row["id"])

    def _finalize_close(self, db_id: int, offline: bool = False) -> None:
        row = self.db.conn.execute("SELECT * FROM trades WHERE id=?",
                                   (db_id,)).fetchone()
        if row is None or row["status"] != "open":
            return
        close_price = row["tp_price"]
        pnl = financing = 0.0
        try:
            t = self.broker.trade(str(row["entry_order_id"]))
            close_price = float(t.get("averageClosePrice") or row["tp_price"])
            pnl = float(t.get("realizedPL") or 0)
            financing = float(t.get("financing") or 0)
        except OandaError as exc:
            log.warning("Detail obchodu %s zlyhal (%s) — použijem tp_price.",
                        row["entry_order_id"], exc)
            pnl = (close_price - row["entry_price"]) * row["qty"] \
                if row["side"] == "long" \
                else (row["entry_price"] - close_price) * row["qty"]
        if financing:
            self.db.add_funding(db_id, datetime.now(timezone.utc)
                                .strftime("%Y-%m-%d"), financing)
        self.db.close_trade(db_id, close_price, pnl)
        self.strategy.on_trade_closed(db_id, row["side"], close_price)
        note = " (počas výpadku)" if offline else ""
        self.tg.send(f"✅ <b>{row['strategy']}</b> ZAVRETÉ {row['side'].upper()} "
                     f"{row['qty']:,.0f} {row['entry_price']:.5f} → "
                     f"{close_price:.5f}{note}\n"
                     f"P/L <b>{pnl:+.2f}</b> (financing {financing:+.2f}, "
                     f"v mene účtu — reálne čísla z Oandy)")
        log.info("ZAVRETÉ db #%d %s @ %.5f, P/L %+.2f%s",
                 db_id, row["side"], close_price, pnl, note)

    # --- alarmy, snapshot, príkazy ---------------------------------------------
    def _dd_alarm(self, mid: float) -> None:
        acct = self._account()
        if not acct:
            return
        nav, upl = acct["NAV"], acct["unrealizedPL"]
        if nav > 0 and upl < 0 and abs(upl) / nav * 100 > self.cfg.DD_ALARM_PCT:
            if not self._dd_alarmed:
                self._dd_alarmed = True
                msg = (f"🚨 Floating DD {abs(upl):,.0f} = "
                       f"{abs(upl) / nav * 100:.1f} % NAV "
                       f"(limit {self.cfg.DD_ALARM_PCT} %).")
                self.tg.send(msg)
                self.db.log_event("alarm", msg)
        elif self._dd_alarmed and (upl >= 0 or abs(upl) / max(nav, 1) * 100
                                   < self.cfg.DD_ALARM_PCT * 0.8):
            self._dd_alarmed = False

    def _daily_snapshot(self) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if day == self._snap_day:
            return
        acct = self._account()
        if not acct:
            return
        yesterday = (datetime.now(timezone.utc).timestamp() - 86400)
        ydate = datetime.fromtimestamp(yesterday, tz=timezone.utc).strftime("%Y-%m-%d")
        self.db.snapshot_account(day, acct["NAV"], acct["balance"],
                                 acct["unrealizedPL"],
                                 len(self.db.open_trades()),
                                 self.db.cycles_on_day(ydate))
        self._snap_day = day

    def _handle_command(self, cmd: str, args: str) -> None:
        if cmd == "/stav":
            acct = self._account()
            reason = self._blocked_reason()
            self.tg.send(
                f"ℹ️ <b>Stav</b> (practice)\n"
                f"NAV {acct.get('NAV', 0):,.2f} {acct.get('currency', '')} | "
                f"floating {acct.get('unrealizedPL', 0):+,.2f}\n"
                f"Pozície: {len(self.db.open_trades())} | poistka: "
                f"{'🚨 AKTÍVNA' if self.failsafe else 'ok'}\n"
                f"Vstupy: {'⏸ ' + reason if reason else '▶️ povolené'}\n"
                f"{self.strategy.status_line()}")
        elif cmd == "/pozicie":
            rows = self.db.open_trades()
            if not rows:
                self.tg.send("Žiadne otvorené pozície.")
                return
            self.tg.send("📋 <b>Pozície</b>\n" + "\n".join(
                f"#{r['id']} {r['side'].upper()} {r['qty']:,.0f} @ "
                f"{r['entry_price']:.5f} → TP {r['tp_price']:.5f}"
                for r in rows))
        elif cmd == "/pauza":
            mins = 60.0
            a = args.strip().lower()
            if a:
                try:
                    mins = float(a[:-1]) * 60 if a.endswith("h") else \
                        float(a.rstrip("m"))
                except ValueError:
                    self.tg.send("Použi /pauza 30m alebo /pauza 2h.")
                    return
            self.paused_until = time.time() + mins * 60
            self.tg.send(f"⏸ Vstupy pozastavené na {mins:.0f} min.")
        elif cmd == "/start":
            self.paused_until = 0.0
            self.tg.send("▶️ Vstupy povolené.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Oanda practice grid bot")
    ap.add_argument("--run-minutes", type=float, default=0.0)
    args = ap.parse_args()
    cfg = OandaBotConfig()
    cfg.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(cfg.LOG_PATH)])
    return OandaBot(cfg).start(run_minutes=args.run_minutes)


if __name__ == "__main__":
    sys.exit(main())
