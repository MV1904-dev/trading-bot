#!/usr/bin/env python3
"""Tretia inštancia bota — cTrader demo (Spotware Open API), nezávislá od
IBKR aj Oanda botov.

Konfigurácia (rovnaká filozofia ako Oanda vetva):
* Grid25-G2B (gap → TP na preskočenú úroveň), pozícia 2 000 EUR
* kapacita 20 úrovní/smer bez rezervy (G3_cap20) + G8 režimová poistka
* Telegram prefix [CTRADER], vlastná DB data/bot_ctrader.db
* TP žije na serveri (relativeTakeProfit pri MARKET orderi)
* zavretia sa detegujú cez reconcile pozícií; realizovaný P/L, swap
  a provízie sa preberajú zo zatvárajúceho dealu (skutočné čísla)

Telegram DEFAULTNE len odosiela — polling príkazov by kradol updaty IBKR
botovi; príkazy sa zapnú s vlastným CTRADER_TELEGRAM_BOT_TOKEN v .env.

Bezpečnosť: demo endpoint natvrdo (DEMO=True); live vyžaduje zmenu
v kóde + env CTRADER_CONFIRM_LIVE="ROZUMIEM-RIZIKU".

.env: CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_ACCESS_TOKEN,
      CTRADER_ACCOUNT_ID
Spustenie: python3 bot_ctrader.py [--run-minutes N]
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
from trading.broker_ctrader import CTraderBroker, CTraderError
from trading.macro import MacroCalendar
from trading.strategy_base import Bar, Signal
from trading.strategy_grid25 import Grid25, Grid25Config
from trading.tg import Telegram

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("bot_ctrader")


@dataclass
class CTraderBotConfig:
    DEMO: bool = True              # natvrdo; live = vedomá zmena + env potvrdenie
    SYMBOL: str = "EURUSD"
    QTY: float = 2_000
    CAP_BASE: int = 20             # G3_cap20 — bez rezervných úrovní
    CAP_RESERVE: int = 0
    FAILSAFE_BAND: float = 0.02    # G8 poistka
    FAILSAFE_RELEASE: float = 0.01
    TICK_SECONDS: float = 10.0
    BAR_SECONDS: int = 300
    ATR_PERIOD: int = 14
    DATA_GAP_ALARM_S: int = 300
    DD_ALARM_PCT: float = 10.0
    TIMEZONE: str = "Europe/Bratislava"
    TG_PREFIX: str = "[CTRADER] "
    DB_PATH: Path = field(default_factory=lambda: ROOT / "data" / "bot_ctrader.db")
    LOG_PATH: Path = field(default_factory=lambda: ROOT / "data" / "bot_ctrader.log")
    CALENDAR_CACHE: Path = field(
        default_factory=lambda: ROOT / "data" / "ff_calendar.json")
    IBKR_M5_CSV: Path = field(
        default_factory=lambda: ROOT / "data" / "ibkr_EURUSD_M5.csv")


class PrefixedTelegram(Telegram):
    def __init__(self, token: str, chat_id: str, prefix: str):
        super().__init__(token, chat_id)
        self.prefix = prefix

    def send(self, text: str, silent: bool = False) -> None:
        super().send(self.prefix + text, silent=silent)


class CTraderBot:
    def __init__(self, cfg: CTraderBotConfig):
        self.cfg = cfg
        self.db = BotDB(cfg.DB_PATH)
        self.tz = ZoneInfo(cfg.TIMEZONE)

        load_dotenv()
        own_token = os.getenv("CTRADER_TELEGRAM_BOT_TOKEN", "")
        self.tg = PrefixedTelegram(
            own_token or os.getenv("TELEGRAM_BOT_TOKEN", ""),
            os.getenv("TELEGRAM_CHAT_ID", ""), cfg.TG_PREFIX)
        self.commands_enabled = bool(own_token)
        if self.commands_enabled:
            self.tg.offset = int(self.db.meta_get("tg_offset", "0") or 0)

        self.broker = CTraderBroker(
            os.getenv("CTRADER_CLIENT_ID", ""),
            os.getenv("CTRADER_CLIENT_SECRET", ""),
            os.getenv("CTRADER_ACCESS_TOKEN", ""),
            os.getenv("CTRADER_ACCOUNT_ID", ""),
            demo=cfg.DEMO, symbol_name=cfg.SYMBOL)

        strat = Grid25(Grid25Config(qty=cfg.QTY, base_levels=cfg.CAP_BASE,
                                    reserve_levels=cfg.CAP_RESERVE))
        strat.id = "Grid25-G2B-CT"
        self.strategy = strat
        self.macro = MacroCalendar(cfg.CALENDAR_CACHE)

        self.atr: float | None = None
        self._atr_prev_close: float | None = None
        self._bar_bucket: int | None = None
        self._bar: Bar | None = None
        self.paused_until = 0.0
        self.auto_paused = False
        self.last_md_ts = time.time()
        self._gap_alarmed = False
        self._dd_alarmed = False
        self._last_close_poll = 0.0
        self._snap_day = ""
        self.daily_closes: list[float] = []
        self._daily_day = ""
        self.failsafe = False

    # ------------------------------------------------------------------ #
    def _guard_demo(self) -> None:
        if self.cfg.DEMO:
            return
        if os.getenv("CTRADER_CONFIRM_LIVE") != "ROZUMIEM-RIZIKU":
            raise SystemExit("CHYBA: DEMO=False vyžaduje env "
                             "CTRADER_CONFIRM_LIVE='ROZUMIEM-RIZIKU'.")

    def start(self, run_minutes: float = 0.0) -> int:
        self._guard_demo()
        try:
            self.broker.connect()
        except CTraderError as exc:
            print(f"CHYBA: cTrader pripojenie zlyhalo: {exc}", file=sys.stderr)
            return 1
        acct = self.broker.account_summary()
        log.info("cTrader demo pripojený, balance %.2f.", acct["balance"])
        self._bootstrap_atr()
        self._load_daily_extremes()
        self._restore_state()
        self.macro.refresh()
        restarted = os.getenv("BOT_RESTARTED") == "1"
        self.tg.send(f"🤖 <b>cTrader bot {'reštartovaný' if restarted else 'spustený'}</b> "
                     f"(demo, {self.cfg.SYMBOL})\n{self.strategy.status_line()}\n"
                     f"Balance: {acct['balance']:,.2f} | pozícia "
                     f"{self.cfg.QTY:,.0f}, kapacita {self.cfg.CAP_BASE}/smer "
                     f"+ G8 poistka")
        self.db.log_event("info", "ctrader bot štart")

        deadline = time.time() + run_minutes * 60 if run_minutes else None
        try:
            while True:
                try:
                    self._tick()
                except Exception:  # noqa: BLE001
                    log.exception("Chyba v ticku")
                    self.db.log_event("warn", "chyba v ticku (pozri log)")
                if deadline and time.time() >= deadline:
                    self.tg.send("🧪 Suchý test dokončený, cTrader bot sa vypína.")
                    break
                time.sleep(self.cfg.TICK_SECONDS)
        except KeyboardInterrupt:
            self.tg.send("🛑 cTrader bot zastavený (Ctrl-C).")
        finally:
            if self.commands_enabled:
                self.db.meta_set("tg_offset", self.tg.offset)
            self.broker.disconnect()
        return 0

    # ------------------------------------------------------------------ #
    def _bootstrap_atr(self) -> None:
        try:
            candles = self.broker.candles_m5(600)
        except CTraderError as exc:
            log.warning("ATR bootstrap zlyhal (%s).", exc)
            return
        n = self.cfg.ATR_PERIOD
        if len(candles) <= n:
            return
        trs = []
        for prev, cur in zip(candles, candles[1:]):
            trs.append(max(cur["h"] - cur["l"], abs(cur["h"] - prev["c"]),
                           abs(cur["l"] - prev["c"])))
        atr = sum(trs[:n]) / n
        for tr in trs[n:]:
            atr = atr * (n - 1) / n + tr / n
        self.atr = atr
        self._atr_prev_close = candles[-1]["c"]
        log.info("ATR(%d, M5) bootstrap: %.6f (%d sviečok).", n, atr, len(candles))

    def _load_daily_extremes(self) -> None:
        closes: dict[str, float] = {}
        try:
            with open(self.cfg.IBKR_M5_CSV, newline="") as f:
                for row in _csv.DictReader(f):
                    closes[row["date"][:10]] = float(row["close"])
        except OSError:
            log.warning("G8 poistka: chýba %s.", self.cfg.IBKR_M5_CSV)
        days = sorted(closes)[-756:]
        self.daily_closes = [closes[d] for d in days]
        self._daily_day = days[-1] if days else ""

    def _restore_state(self) -> None:
        rows = self.db.open_trades()
        if not rows:
            return
        try:
            open_ids = self.broker.open_position_ids()
        except CTraderError as exc:
            log.warning("Obnova: reconcile zlyhal (%s).", exc)
            return
        recovered = closed_offline = 0
        oldest_ms = int(min(r["ts_open"] for r in rows) * 1000)
        deals = {}
        try:
            deals = self.broker.closed_deals_since(oldest_ms)
        except CTraderError:
            pass
        for row in rows:
            if row["entry_order_id"] in open_ids:
                recovered += 1
            else:
                self._finalize_close(row["id"], deals, offline=True)
                closed_offline += 1
        self.strategy.restore(self.db.open_trades())
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
            self._update_failsafe_daily(px["mid"])
            self._aggregate_bar(px["mid"])
        self._poll_closes()
        self._daily_snapshot()
        self.macro.refresh()
        if self.commands_enabled:
            self.tg.poll_commands(self._handle_command)
            self.db.meta_set("tg_offset", self.tg.offset)

    def _price(self) -> dict | None:
        q = self.broker.quote()
        if q is None or q["age_s"] > 120 or not self.broker.is_connected():
            self._maybe_gap_alarm()
            return None
        self.last_md_ts = time.time()
        if self._gap_alarmed:
            self._gap_alarmed = False
            self.auto_paused = False
            self.tg.send("✅ Stream znovu beží, pauza zrušená.")
        return q

    def _maybe_gap_alarm(self) -> None:
        stale = time.time() - self.last_md_ts
        if stale > self.cfg.DATA_GAP_ALARM_S and not self._gap_alarmed:
            self.auto_paused = True
            self._gap_alarmed = True
            msg = (f"🚨 cTrader stream/API nedostupné > {int(stale // 60)} min "
                   f"— nové vstupy stoja, TP bežia na serveri.")
            self.tg.send(msg)
            self.db.log_event("alarm", msg)

    def _update_failsafe_daily(self, mid: float) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if day != self._daily_day and self._daily_day:
            self.daily_closes.append(mid)
            self.daily_closes = self.daily_closes[-756:]
            self._daily_day = day
        elif not self._daily_day:
            self._daily_day = day
        if len(self.daily_closes) < 250:
            return
        hi, lo = max(self.daily_closes), min(self.daily_closes)
        band, rel = self.cfg.FAILSAFE_BAND, self.cfg.FAILSAFE_RELEASE
        if not self.failsafe and (mid > hi * (1 + band) or mid < lo * (1 - band)):
            self.failsafe = True
            new_cap = max(int(self.cfg.CAP_BASE * 0.5), 1)
            self.strategy.cfg.base_levels = new_cap
            msg = (f"🚨 <b>G8 poistka AKTÍVNA</b>: kurz {mid:.5f} > {band:.0%} "
                   f"za 3r extrémom ({lo:.5f}–{hi:.5f}); kapacita {new_cap}/smer.")
            self.tg.send(msg)
            self.db.log_event("alarm", msg)
        elif self.failsafe and lo * (1 - rel) < mid < hi * (1 + rel):
            self.failsafe = False
            self.strategy.cfg.base_levels = self.cfg.CAP_BASE
            self.tg.send(f"✅ G8 poistka uvoľnená, kapacita {self.cfg.CAP_BASE}/smer.")

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
            return "auto-pauza (výpadok streamu/API)"
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
            return
        units = sig.qty if sig.side == "long" else -sig.qty
        try:
            res = self.broker.market_order_with_tp(units, sig.tp_price,
                                                   tag=sig.strategy_id)
        except CTraderError as exc:
            self.db.log_signal(sig.strategy_id, sig.side, bar.close,
                               self.atr or 0.0, 0.0, "error", str(exc),
                               sig.context)
            log.warning("Vstup zlyhal: %s", exc)
            return
        ctx = dict(sig.context)
        ctx.update({"reason": sig.reason, "failsafe": self.failsafe})
        trade_id = self.db.open_trade(
            sig.strategy_id, sig.side, sig.qty, res["price"], sig.tp_price,
            res["position_id"], res["order_id"], 0.0, ctx)
        self.strategy.on_trade_opened(trade_id, sig.side, res["price"])
        self.db.log_signal(sig.strategy_id, sig.side, bar.close,
                           self.atr or 0.0, 0.0, "executed", sig.reason, ctx)
        self.tg.send(f"📈 <b>{sig.strategy_id}</b> OTVORENÉ {sig.side.upper()} "
                     f"{sig.qty:,.0f} {self.cfg.SYMBOL} @ {res['price']:.5f}\n"
                     f"TP {sig.tp_price:.5f} (na serveri) | ATR {self.atr:.5f}\n"
                     f"dôvod: {sig.reason}")
        log.info("OTVORENÉ %s @ %.5f (pozícia %s, db #%d)",
                 sig.side, res["price"], res["position_id"], trade_id)

    def _poll_closes(self) -> None:
        now = time.time()
        if now - self._last_close_poll < 30:
            return
        self._last_close_poll = now
        rows = self.db.open_trades()
        if not rows:
            return
        try:
            open_ids = self.broker.open_position_ids()
        except CTraderError:
            return
        missing = [r for r in rows if r["entry_order_id"] not in open_ids]
        if not missing:
            return
        oldest_ms = int(min(r["ts_open"] for r in missing) * 1000)
        deals = {}
        try:
            deals = self.broker.closed_deals_since(oldest_ms)
        except CTraderError:
            pass
        for row in missing:
            self._finalize_close(row["id"], deals)

    def _finalize_close(self, db_id: int, deals: dict,
                        offline: bool = False) -> None:
        row = self.db.conn.execute("SELECT * FROM trades WHERE id=?",
                                   (db_id,)).fetchone()
        if row is None or row["status"] != "open":
            return
        deal = deals.get(row["entry_order_id"])
        if deal:
            close_price = deal["close_price"] or row["tp_price"]
            pnl = deal["gross"]
            swap = deal["swap"]
            comm = deal["commission"]
        else:
            close_price = row["tp_price"]
            pnl = (close_price - row["entry_price"]) * row["qty"] \
                if row["side"] == "long" \
                else (row["entry_price"] - close_price) * row["qty"]
            swap = comm = 0.0
        if swap:
            self.db.add_funding(db_id, datetime.now(timezone.utc)
                                .strftime("%Y-%m-%d"), swap)
        self.db.close_trade(db_id, close_price, pnl, comm)
        self.strategy.on_trade_closed(db_id, row["side"], close_price)
        note = " (počas výpadku)" if offline else ""
        self.tg.send(f"✅ <b>{row['strategy']}</b> ZAVRETÉ {row['side'].upper()} "
                     f"{row['qty']:,.0f} {row['entry_price']:.5f} → "
                     f"{close_price:.5f}{note}\n"
                     f"P/L <b>{pnl:+.2f}</b> (swap {swap:+.2f}, provízie "
                     f"−{comm:.2f}; reálne čísla z dealu)")
        log.info("ZAVRETÉ db #%d %s @ %.5f, P/L %+.2f%s",
                 db_id, row["side"], close_price, pnl, note)

    def _daily_snapshot(self) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if day == self._snap_day:
            return
        try:
            acct = self.broker.account_summary()
        except CTraderError:
            return
        ydate = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - 86400,
            tz=timezone.utc).strftime("%Y-%m-%d")
        self.db.snapshot_account(day, acct["balance"], acct["balance"], 0.0,
                                 len(self.db.open_trades()),
                                 self.db.cycles_on_day(ydate))
        self._snap_day = day

    def _handle_command(self, cmd: str, args: str) -> None:
        if cmd == "/stav":
            try:
                acct = self.broker.account_summary()
                bal = acct["balance"]
            except CTraderError:
                bal = 0.0
            reason = self._blocked_reason()
            self.tg.send(f"ℹ️ <b>Stav</b> (demo)\nBalance {bal:,.2f}\n"
                         f"Pozície: {len(self.db.open_trades())} | poistka: "
                         f"{'🚨' if self.failsafe else 'ok'}\n"
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
    ap = argparse.ArgumentParser(description="cTrader demo grid bot")
    ap.add_argument("--run-minutes", type=float, default=0.0)
    args = ap.parse_args()
    cfg = CTraderBotConfig()
    cfg.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(cfg.LOG_PATH)])
    return CTraderBot(cfg).start(run_minutes=args.run_minutes)


if __name__ == "__main__":
    sys.exit(main())
