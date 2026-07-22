#!/usr/bin/env python3
"""Živý multi-stratégiový bot proti IBKR paper účtu.

Spustenie:
    python3 bot.py                    # beží, kým ho nezastavíš (Ctrl-C)
    python3 bot.py --run-minutes 10   # suchý test — po N minútach skončí

Architektúra: stratégie (trading/strategy_*.py) generujú signály na uzavretých
M5 baroch; spoločná exekučná + risk vrstva tu rozhoduje, či sa vykonajú
(pauza, makro blackout, výpadok dát, kapacita), všetko loguje do SQLite
(data/bot.db) a notifikuje cez Telegram. Každý príkaz nesie orderRef
s ID stratégie.

Bezpečnosť: MODE je natvrdo "paper" (trading/bot_config.py). Prepnutie na
live vyžaduje zmenu v kóde + env BOT_CONFIRM_LIVE="ROZUMIEM-RIZIKU".
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from ib_async import LimitOrder, MarketOrder

from trading.bot_config import BotConfig
from trading.botdb import BotDB
from trading.broker_ibkr import IBKRBroker
from trading.macro import MacroCalendar
from trading.rates import daily_funding_usd
from trading.strategy_base import Bar, Signal
from trading.strategy_grid25 import Grid25
from trading.tg import Telegram

log = logging.getLogger("bot")


class Bot:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.db = BotDB(cfg.DB_PATH)
        self.tz = ZoneInfo(cfg.TIMEZONE)

        load_dotenv()
        self.tg = Telegram(os.getenv("TELEGRAM_BOT_TOKEN", ""),
                           os.getenv("TELEGRAM_CHAT_ID", ""))
        self.tg.offset = int(self.db.meta_get("tg_offset", "0") or 0)

        self.macro = MacroCalendar(cfg.CALENDAR_CACHE)
        self.broker = IBKRBroker(host=cfg.HOST, port=cfg.PORT,
                                 client_id=cfg.CLIENT_ID)
        self.strategies = [Grid25()]          # ďalšie stratégie sem

        # runtime stav
        self.contract = None
        self.ticker = None
        self.atr: float | None = None
        self._atr_prev_close: float | None = None
        self._bar_bucket: int | None = None
        self._bar: Bar | None = None
        self.tp_trades: dict[int, object] = {}    # db trade_id -> ib Trade
        self.paused_until: float = 0.0            # manuálna pauza (/pauza)
        self.auto_paused: bool = False            # výpadok dát/Gateway
        self.last_md_ts: float = time.time()
        self.conn_down_since: float | None = None
        self._gap_alarmed = False
        self._band_alerted: str | None = None
        self._blackout_notified: float = 0.0
        self._dd_alarmed = False
        self._brief_date: str = self.db.meta_get("brief_date", "")
        self._snap_day: str = ""
        self._funding_day: str = self.db.meta_get(
            "funding_day", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    # ------------------------------------------------------------------ #
    # Štart / guardy
    # ------------------------------------------------------------------ #
    def _guard_paper(self) -> None:
        c = self.cfg
        if c.MODE == "paper":
            if c.PORT not in (4002, 7497):
                raise SystemExit(f"CHYBA: MODE=paper, ale port {c.PORT} nie je "
                                 "paper port (4002/7497).")
            return
        if os.getenv("BOT_CONFIRM_LIVE") != "ROZUMIEM-RIZIKU":
            raise SystemExit(
                "CHYBA: MODE != paper. Live prevádzka vyžaduje vedomú zmenu: "
                "env BOT_CONFIRM_LIVE='ROZUMIEM-RIZIKU'. Bot končí.")

    def _connect_with_retry(self) -> None:
        """Čaká na Gateway s backoffom namiesto pádu (Gateway sa cez noc
        reštartuje / odhlasuje). Alarm pošle raz po DATA_GAP_ALARM_S."""
        delay, waited, alarmed = 15.0, 0.0, False
        while True:
            try:
                self.broker.connect()
                if alarmed:
                    self.tg.send("✅ Gateway znovu dostupný, bot pokračuje.")
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("Gateway nedostupný (%s) — ďalší pokus o %.0f s.",
                            exc, delay)
                if waited >= self.cfg.DATA_GAP_ALARM_S and not alarmed:
                    alarmed = True
                    self.tg.send(f"🚨 Gateway nedostupný > "
                                 f"{int(waited // 60)} min — bot čaká na "
                                 f"pripojenie (port {self.cfg.PORT}).")
                    self.db.log_event("alarm", "Gateway nedostupný, čakám")
                time.sleep(delay)
                waited += delay
                delay = min(delay * 2, 300.0)

    def start(self, run_minutes: float = 0.0) -> int:
        self._guard_paper()
        self._connect_with_retry()
        self.contract = self.broker.forex(self.cfg.PAIR)
        self.ticker = self.broker.ib.reqMktData(self.contract, "", False, False)
        self._bootstrap_atr()
        self._restore_state()
        self.macro.refresh()

        restarted = os.getenv("BOT_RESTARTED") == "1"
        msg = (f"🤖 <b>Bot {'reštartovaný' if restarted else 'spustený'}</b> "
               f"({self.cfg.MODE}, {self.cfg.PAIR})\n"
               + "\n".join(s.status_line() for s in self.strategies))
        self.tg.send(msg)
        self.db.log_event("info", "bot štart" + (" (reštart)" if restarted else ""))
        log.info("Bot beží (mode=%s, pair=%s, clientId=%s).",
                 self.cfg.MODE, self.cfg.PAIR, self.cfg.CLIENT_ID)

        deadline = time.time() + run_minutes * 60 if run_minutes else None
        try:
            while True:
                try:
                    self._tick()
                except Exception:  # noqa: BLE001 — jednotlivý tick nesmie zhodiť bota
                    log.exception("Chyba v ticku")
                    self.db.log_event("warn", "chyba v ticku (pozri log)")
                if deadline and time.time() >= deadline:
                    log.info("Uplynul --run-minutes limit, končím.")
                    self.tg.send("🧪 Suchý test dokončený, bot sa vypína.")
                    break
                self.broker.ib.sleep(self.cfg.TICK_SECONDS)
        except KeyboardInterrupt:
            log.info("Prerušené používateľom.")
            self.tg.send("🛑 Bot zastavený (Ctrl-C).")
        finally:
            self.db.meta_set("tg_offset", self.tg.offset)
            self.broker.disconnect()
        return 0

    # ------------------------------------------------------------------ #
    # Bootstrap + obnova stavu
    # ------------------------------------------------------------------ #
    def _bootstrap_atr(self) -> None:
        df = self.broker.history(self.contract, bar_size="5 mins",
                                 duration="2 D")
        if not len(df):
            log.warning("ATR bootstrap: história nedostupná, ATR sa dopočíta "
                        "z živých barov.")
            return
        n = self.cfg.ATR_PERIOD
        closes = df["close"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        trs = []
        for i in range(1, len(df)):
            trs.append(max(highs[i] - lows[i],
                           abs(highs[i] - closes[i - 1]),
                           abs(lows[i] - closes[i - 1])))
        if len(trs) < n:
            return
        atr = sum(trs[:n]) / n
        for tr in trs[n:]:
            atr = atr * (n - 1) / n + tr / n
        self.atr = atr
        self._atr_prev_close = closes[-1]
        log.info("ATR(%d, M5) bootstrap: %.6f (%d barov histórie).",
                 n, atr, len(df))

    def _restore_state(self) -> None:
        """Obnova po reštarte: DB open trades + spárovanie s IBKR."""
        rows = self.db.open_trades()
        if not rows:
            return
        open_by_ref = {}
        for t in self.broker.ib.openTrades():
            ref = getattr(t.order, "orderRef", "") or ""
            open_by_ref[ref] = t

        recovered, closed_offline = 0, 0
        for row in rows:
            ref = f"{row['strategy']}:{row['id']}"
            t = open_by_ref.get(ref)
            if t is not None:
                self.tp_trades[row["id"]] = t
                recovered += 1
            else:
                # TP limitka už nie je aktívna → považujeme ju za naplnenú
                # počas výpadku (rekonštrukcia; cena = tp_price).
                side = row["side"]
                qty = row["qty"]
                tp = row["tp_price"]
                pnl = (tp - row["entry_price"]) * qty if side == "long" \
                    else (row["entry_price"] - tp) * qty
                self.db.close_trade(row["id"], tp, pnl)
                closed_offline += 1

        still_open = self.db.open_trades()
        for s in self.strategies:
            s.restore([r for r in still_open if r["strategy"] == s.id])

        # kontrola voči netto pozícii na IBKR
        expected = sum(r["qty"] if r["side"] == "long" else -r["qty"]
                       for r in still_open)
        ib_net = 0.0
        for p in self.broker.positions():
            if p["symbol"] in (self.cfg.PAIR, "EUR"):
                ib_net = p["position"]
        note = (f"Obnova stavu: {recovered} pozícií spárovaných, "
                f"{closed_offline} TP naplnených počas výpadku.")
        log.info("%s DB net=%s, IBKR net=%s", note, expected, ib_net)
        self.db.log_event("info", note)
        if abs(expected - ib_net) > 1:
            warn = (f"⚠️ Nesúlad pozícií po obnove: DB {expected:+.0f} vs "
                    f"IBKR {ib_net:+.0f} {self.cfg.PAIR}. Skontroluj manuálne.")
            self.tg.send(warn)
            self.db.log_event("warn", warn)

    # ------------------------------------------------------------------ #
    # Hlavný tick
    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        self._watch_connection()
        mid = self._current_mid()
        if mid is not None:
            self._aggregate_bar(mid)
            self._alarms(mid)
        self._check_tp_fills()
        self._accrue_funding(mid)
        self._daily_snapshot(mid)
        self._morning_briefing(mid)
        self.macro.refresh()
        self._notify_blackout()
        self.tg.poll_commands(self._handle_command)
        self.db.meta_set("tg_offset", self.tg.offset)

    # --- pripojenie a dáta -------------------------------------------------
    def _watch_connection(self) -> None:
        ib = self.broker.ib
        if ib.isConnected():
            if self.conn_down_since is not None:
                log.info("Gateway spojenie obnovené.")
                self.tg.send("✅ Spojenie s Gateway obnovené.")
                self.conn_down_since = None
            return
        if self.conn_down_since is None:
            self.conn_down_since = time.time()
            log.warning("Gateway odpojený, skúšam reconnect…")
        try:
            self.broker.connect()
            self.ticker = ib.reqMktData(self.contract, "", False, False)
        except Exception as exc:  # noqa: BLE001
            log.warning("Reconnect zlyhal: %s", exc)
        self._maybe_gap_alarm("Gateway nedostupný")

    def _current_mid(self) -> float | None:
        t = self.ticker
        if t is None:
            return None
        bid, ask = t.bid, t.ask
        if bid and ask and bid > 0 and ask > 0:
            self.last_md_ts = time.time()
            if self._gap_alarmed or self.auto_paused:
                self.auto_paused = False
                self._gap_alarmed = False
                self.tg.send("✅ Dáta znovu tečú, automatická pauza zrušená.")
                self.db.log_event("info", "dáta obnovené, auto-pauza zrušená")
            return (bid + ask) / 2
        self._maybe_gap_alarm("výpadok market dát")
        return None

    def _maybe_gap_alarm(self, why: str) -> None:
        stale = time.time() - self.last_md_ts
        if stale > self.cfg.DATA_GAP_ALARM_S and not self._gap_alarmed:
            self.auto_paused = True
            self._gap_alarmed = True
            msg = (f"🚨 {why} > {int(stale // 60)} min — nové vstupy "
                   f"pozastavené, existujúce TP bežia.")
            self.tg.send(msg)
            self.db.log_event("alarm", msg)

    # --- bar agregácia a signály ------------------------------------------
    def _aggregate_bar(self, mid: float) -> None:
        now = time.time()
        bucket = int(now // self.cfg.BAR_SECONDS)
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
        # bar sa uzavrel
        closed = self._bar
        self._bar_bucket = bucket
        self._bar = Bar(bucket * self.cfg.BAR_SECONDS, mid, mid, mid, mid)
        self._update_atr(closed)
        for s in self.strategies:
            if not s.enabled:
                continue
            for sig in s.on_bar(closed, self.atr):
                self._execute(s, sig, closed)

    def _update_atr(self, bar: Bar) -> None:
        n = self.cfg.ATR_PERIOD
        pc = self._atr_prev_close if self._atr_prev_close is not None else bar.open
        tr = max(bar.high - bar.low, abs(bar.high - pc), abs(bar.low - pc))
        self.atr = tr if self.atr is None else self.atr * (n - 1) / n + tr / n
        self._atr_prev_close = bar.close

    # --- exekučná + risk vrstva -------------------------------------------
    def _blocked_reason(self) -> str | None:
        if self.auto_paused:
            return "auto-pauza (výpadok dát/Gateway)"
        if time.time() < self.paused_until:
            left = int((self.paused_until - time.time()) // 60) + 1
            return f"manuálna pauza (ešte ~{left} min)"
        ev = self.macro.active_blackout()
        if ev:
            t = datetime.fromtimestamp(ev["ts"], self.tz).strftime("%H:%M")
            return f"makro blackout: {ev['currency']} {ev['title']} o {t}"
        if not self.broker.ib.isConnected():
            return "Gateway odpojený"
        return None

    def _execute(self, strat, sig: Signal, bar: Bar) -> None:
        spread = (self.ticker.ask - self.ticker.bid) \
            if (self.ticker and self.ticker.bid and self.ticker.ask) else 0.0
        reason = self._blocked_reason()
        if reason:
            self.db.log_signal(sig.strategy_id, sig.side, bar.close,
                               self.atr or 0.0, spread, "blocked", reason,
                               sig.context)
            log.info("Signál %s %s BLOKOVANÝ: %s", sig.strategy_id, sig.side, reason)
            return

        action = "BUY" if sig.side == "long" else "SELL"
        order = MarketOrder(action, sig.qty)
        order.orderRef = sig.strategy_id
        trade = self.broker.ib.placeOrder(self.contract, order)
        for _ in range(50):                      # max ~15 s na fill
            self.broker.ib.sleep(0.3)
            if trade.orderStatus.status in ("Filled", "Cancelled", "Inactive"):
                break
        if trade.orderStatus.status != "Filled":
            self.broker.ib.cancelOrder(order)
            self.db.log_signal(sig.strategy_id, sig.side, bar.close,
                               self.atr or 0.0, spread, "error",
                               f"vstup nenaplnený ({trade.orderStatus.status})",
                               sig.context)
            return

        fill = trade.orderStatus.avgFillPrice
        comm = sum(abs(f.commissionReport.commission) for f in trade.fills
                   if f.commissionReport) or 0.0
        # TP zo signálu (G2B gap TP môže byť širší než +tp_pct); poistka:
        # ak fill preskočil TP zo signálu, padni späť na fill ± tp_pct.
        tp_pct = getattr(strat.cfg, "tp_pct", 0.001)
        tp_price = sig.tp_price
        if sig.side == "long" and tp_price <= fill:
            tp_price = round(fill * (1 + tp_pct), 5)
        elif sig.side == "short" and tp_price >= fill:
            tp_price = round(fill * (1 - tp_pct), 5)

        ctx = dict(sig.context)
        ctx.update({"spread": spread, "atr": self.atr, "reason": sig.reason,
                    "bar_close": bar.close})
        trade_id = self.db.open_trade(
            sig.strategy_id, sig.side, sig.qty, fill, tp_price,
            trade.order.orderId, 0, comm, ctx)

        tp_action = "SELL" if sig.side == "long" else "BUY"
        tp_order = LimitOrder(tp_action, sig.qty, tp_price)
        tp_order.tif = "GTC"
        tp_order.orderRef = f"{sig.strategy_id}:{trade_id}"
        tp_trade = self.broker.ib.placeOrder(self.contract, tp_order)
        self.broker.ib.sleep(0.3)
        self.db.set_tp_order(trade_id, tp_order.orderId, tp_price)
        self.tp_trades[trade_id] = tp_trade

        strat.on_trade_opened(trade_id, sig.side, fill)
        self.db.log_signal(sig.strategy_id, sig.side, bar.close,
                           self.atr or 0.0, spread, "executed", sig.reason, ctx)
        msg = (f"📈 <b>{sig.strategy_id}</b> OTVORENÉ {sig.side.upper()} "
               f"{sig.qty:,.0f} {self.cfg.PAIR} @ {fill:.5f}\n"
               f"TP {tp_price:.5f} | ATR {self.atr:.5f} | spread {spread:.5f}\n"
               f"dôvod: {sig.reason}")
        self.tg.send(msg)
        log.info("OTVORENÉ %s %s @ %.5f (TP %.5f, trade_id=%d)",
                 sig.side, sig.strategy_id, fill, tp_price, trade_id)

    def _check_tp_fills(self) -> None:
        for trade_id, t in list(self.tp_trades.items()):
            if t.orderStatus.status != "Filled":
                continue
            row = self.db.conn.execute("SELECT * FROM trades WHERE id=?",
                                       (trade_id,)).fetchone()
            if row is None:
                self.tp_trades.pop(trade_id, None)
                continue
            fill = t.orderStatus.avgFillPrice or row["tp_price"]
            comm = sum(abs(f.commissionReport.commission) for f in t.fills
                       if f.commissionReport) or 0.0
            pnl = (fill - row["entry_price"]) * row["qty"] if row["side"] == "long" \
                else (row["entry_price"] - fill) * row["qty"]
            self.db.close_trade(trade_id, fill, pnl, comm)
            self.tp_trades.pop(trade_id, None)
            for s in self.strategies:
                if s.id == row["strategy"]:
                    s.on_trade_closed(trade_id, row["side"], fill)
            total = pnl + row["funding_usd"] - comm - row["commission_usd"]
            msg = (f"✅ <b>{row['strategy']}</b> ZAVRETÉ {row['side'].upper()} "
                   f"{row['qty']:,.0f} {self.cfg.PAIR} "
                   f"{row['entry_price']:.5f} → {fill:.5f}\n"
                   f"P/L {pnl:+.2f} USD (funding {row['funding_usd']:+.2f}, "
                   f"provízie −{comm + row['commission_usd']:.2f}) "
                   f"= <b>{total:+.2f} USD</b>")
            self.tg.send(msg)
            log.info("ZAVRETÉ trade_id=%d %s @ %.5f, P/L %.2f USD",
                     trade_id, row["side"], fill, pnl)

    # --- funding, snapshoty, briefing --------------------------------------
    def _accrue_funding(self, mid: float | None) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today == self._funding_day or mid is None:
            return
        d0 = datetime.strptime(self._funding_day, "%Y-%m-%d").date()
        d1 = datetime.strptime(today, "%Y-%m-%d").date()
        ndays = max((d1 - d0).days, 1)
        for row in self.db.open_trades():
            amt = daily_funding_usd(today, row["side"], row["qty"], mid) * ndays
            self.db.add_funding(row["id"], today, amt)
        self._funding_day = today
        self.db.meta_set("funding_day", today)
        log.info("Funding pripísaný za %d deň/dni.", ndays)

    def _floating_usd(self, mid: float | None) -> float:
        if mid is None:
            return 0.0
        out = 0.0
        for r in self.db.open_trades():
            out += (mid - r["entry_price"]) * r["qty"] if r["side"] == "long" \
                else (r["entry_price"] - mid) * r["qty"]
        return out

    def _daily_snapshot(self, mid: float | None) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today == self._snap_day:
            return
        try:
            summ = self.broker.account_summary()
            net = float(summ.get("NetLiquidation", 0) or 0)
            cash = float(summ.get("TotalCashValue", 0) or 0)
        except Exception:  # noqa: BLE001
            net = cash = 0.0
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        self.db.snapshot_account(today, net, cash, self._floating_usd(mid),
                                 len(self.db.open_trades()),
                                 self.db.cycles_on_day(yesterday))
        self._snap_day = today

    def _morning_briefing(self, mid: float | None) -> None:
        now = datetime.now(self.tz)
        today = now.strftime("%Y-%m-%d")
        if (not self.cfg.BRIEFING_HOUR <= now.hour < self.cfg.BRIEFING_HOUR_END
                or self._brief_date == today):
            return
        self._brief_date = today
        self.db.meta_set("brief_date", today)

        try:
            summ = self.broker.account_summary()
            net = float(summ.get("NetLiquidation", 0) or 0)
        except Exception:  # noqa: BLE001
            net = 0.0
        floating = self._floating_usd(mid)
        rows = self.db.open_trades()
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        cycles = self.db.cycles_on_day(yesterday)
        events = self.macro.todays_events(self.tz)
        ev_txt = "\n".join(
            f"  • {datetime.fromtimestamp(e['ts'], self.tz):%H:%M} "
            f"{e['currency']} {e['title']}" for e in events) or "  (žiadne)"
        longs = sum(1 for r in rows if r["side"] == "long")
        shorts = len(rows) - longs
        kurz = f"Kurz: {mid:.5f}\n" if mid else ""
        msg = (f"☀️ <b>Ranný briefing</b> {now:%d.%m.%Y}\n"
               f"Účet: {net:,.0f} | floating P/L {floating:+,.0f} USD\n"
               f"Pozície: {len(rows)} (long {longs} / short {shorts})\n"
               f"{kurz}"
               f"Včerajšie cykly: {cycles}\n"
               f"Dnešné high-impact udalosti (USD/EUR):\n{ev_txt}")
        self.tg.send(msg)

    # --- alarmy ------------------------------------------------------------
    def _alarms(self, mid: float) -> None:
        c = self.cfg
        # pásmový alarm s hysterézou
        if mid < c.BAND_ALERT_LOW and self._band_alerted != "low":
            self._band_alerted = "low"
            self.tg.send(f"⚠️ Kurz {mid:.5f} < {c.BAND_ALERT_LOW} — "
                         f"opúšťa obojsmerné pásmo (blíži sa k {1.1200}).")
            self.db.log_event("alarm", f"kurz {mid:.5f} pod {c.BAND_ALERT_LOW}")
        elif mid > c.BAND_ALERT_HIGH and self._band_alerted != "high":
            self._band_alerted = "high"
            self.tg.send(f"⚠️ Kurz {mid:.5f} > {c.BAND_ALERT_HIGH} — "
                         f"opúšťa obojsmerné pásmo (blíži sa k {1.1600}).")
            self.db.log_event("alarm", f"kurz {mid:.5f} nad {c.BAND_ALERT_HIGH}")
        elif (self._band_alerted == "low"
              and mid > c.BAND_ALERT_LOW + c.BAND_ALERT_RESET) or \
             (self._band_alerted == "high"
              and mid < c.BAND_ALERT_HIGH - c.BAND_ALERT_RESET):
            self._band_alerted = None

        # floating drawdown alarm
        try:
            net = float(self.broker.account_summary()
                        .get("NetLiquidation", 0) or 0)
        except Exception:  # noqa: BLE001
            net = 0.0
        floating = self._floating_usd(mid)
        if net > 0 and floating < 0 and abs(floating) / net * 100 > c.DD_ALARM_PCT:
            if not self._dd_alarmed:
                self._dd_alarmed = True
                msg = (f"🚨 Floating DD {abs(floating):,.0f} USD "
                       f"= {abs(floating) / net * 100:.1f} % kapitálu "
                       f"(limit {c.DD_ALARM_PCT} %).")
                self.tg.send(msg)
                self.db.log_event("alarm", msg)
        elif self._dd_alarmed and (net <= 0 or abs(min(floating, 0)) / max(net, 1)
                                   * 100 < c.DD_ALARM_PCT * 0.8):
            self._dd_alarmed = False

    def _notify_blackout(self) -> None:
        ev = self.macro.active_blackout()
        if ev and ev["ts"] != self._blackout_notified:
            self._blackout_notified = ev["ts"]
            t = datetime.fromtimestamp(ev["ts"], self.tz).strftime("%H:%M")
            self.tg.send(f"📅 Makro blackout ±30 min: {ev['currency']} "
                         f"<b>{ev['title']}</b> o {t} — nové vstupy stoja, "
                         f"TP bežia ďalej.")

    # --- Telegram príkazy ---------------------------------------------------
    def _handle_command(self, cmd: str, args: str) -> None:
        if cmd == "/stav":
            mid = self._current_mid()
            reason = self._blocked_reason()
            try:
                net = float(self.broker.account_summary()
                            .get("NetLiquidation", 0) or 0)
            except Exception:  # noqa: BLE001
                net = 0.0
            rows = self.db.open_trades()
            self.tg.send(
                f"ℹ️ <b>Stav bota</b> ({self.cfg.MODE})\n"
                f"Gateway: {'✅' if self.broker.ib.isConnected() else '❌'} | "
                f"kurz: {f'{mid:.5f}' if mid else '—'}\n"
                f"Účet: {net:,.0f} | floating {self._floating_usd(mid):+,.0f} USD\n"
                f"Pozície: {len(rows)} | vstupy: "
                f"{'⏸ ' + reason if reason else '▶️ povolené'}\n"
                + "\n".join(s.status_line() for s in self.strategies))
        elif cmd == "/pozicie":
            rows = self.db.open_trades()
            if not rows:
                self.tg.send("Žiadne otvorené pozície.")
                return
            lines = []
            for r in rows:
                age_h = (time.time() - r["ts_open"]) / 3600
                lines.append(f"#{r['id']} {r['side'].upper()} {r['qty']:,.0f} "
                             f"@ {r['entry_price']:.5f} → TP {r['tp_price']:.5f} "
                             f"| {age_h:.1f} h | fund {r['funding_usd']:+.2f}")
            self.tg.send("📋 <b>Pozície</b>\n" + "\n".join(lines))
        elif cmd == "/pauza":
            minutes = 60.0
            a = args.strip().lower()
            if a:
                try:
                    if a.endswith("h"):
                        minutes = float(a[:-1]) * 60
                    elif a.endswith("m"):
                        minutes = float(a[:-1])
                    else:
                        minutes = float(a)
                except ValueError:
                    self.tg.send("Nerozumiem trvaniu — použi napr. "
                                 "/pauza 30m alebo /pauza 2h.")
                    return
            self.paused_until = time.time() + minutes * 60
            self.db.log_event("info", f"manuálna pauza {minutes:.0f} min")
            self.tg.send(f"⏸ Nové vstupy pozastavené na {minutes:.0f} min. "
                         f"Existujúce TP bežia. /start ich obnoví skôr.")
        elif cmd == "/start":
            self.paused_until = 0.0
            self.db.log_event("info", "manuálna pauza zrušená")
            self.tg.send("▶️ Vstupy znovu povolené.")
        else:
            self.tg.send("Príkazy: /stav /pozicie /pauza [30m|2h] /start")


def main() -> int:
    ap = argparse.ArgumentParser(description="IBKR paper trading bot")
    ap.add_argument("--run-minutes", type=float, default=0.0,
                    help="suchý test: skonči po N minútach")
    args = ap.parse_args()

    cfg = BotConfig()
    cfg.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(cfg.LOG_PATH)])
    logging.getLogger("ib_async").setLevel(logging.WARNING)

    return Bot(cfg).start(run_minutes=args.run_minutes)


if __name__ == "__main__":
    sys.exit(main())
