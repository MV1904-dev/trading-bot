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
import json
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
from trading.strategy_s7 import S7Config, S7Continuation
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
    S7_ENABLED: bool = False    # NEPREŠIEL nezávislým overením — viď trading/strategy_s7.py
    S7_QTY: float = 2_000
    FAILSAFE_BAND: float = 0.02    # G8 poistka
    FAILSAFE_RELEASE: float = 0.01
    TICK_SECONDS: float = 10.0
    BAR_SECONDS: int = 300
    ATR_PERIOD: int = 14
    DATA_GAP_ALARM_S: int = 300
    HARD_RESTART_S: int = 900   # mŕtvy stream > 15 min → reštart procesu
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
            refresh_token=os.getenv("CTRADER_REFRESH_TOKEN", ""),
            env_path=str(ROOT / ".env"),
            demo=cfg.DEMO, symbol_name=cfg.SYMBOL)
        self.broker.on_token_refreshed = lambda: self.tg.send(
            "🔑 Access token obnovený cez refresh token (uložený do .env).")
        self.broker.on_token_refresh_failed = lambda err: (
            self.tg.send(f"🚨 Obnova access tokenu ZLYHALA: {err} — "
                         f"vygeneruj nový v Open API portáli."),
            self.db.log_event("alarm", f"token refresh zlyhal: {err}"))

        grid = Grid25(Grid25Config(qty=cfg.QTY, base_levels=cfg.CAP_BASE,
                                   reserve_levels=cfg.CAP_RESERVE))
        grid.id = "Grid25-G2B-CT"
        s7 = S7Continuation(S7Config(qty=cfg.S7_QTY))
        s7.enabled = cfg.S7_ENABLED
        self.strategies = [grid, s7]
        self.strategy = grid          # spätná kompatibilita (kotvy, status)
        self.macro = MacroCalendar(cfg.CALENDAR_CACHE)

        self._tfs = sorted({s.timeframe_s for s in self.strategies if s.enabled})
        self._bars: dict[int, dict] = {}        # tf -> {"bucket","bar"}
        self._atr: dict[int, float | None] = {}
        self._atr_prev: dict[int, float | None] = {}
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
                     f"(demo, {self.cfg.SYMBOL})\n"
                     + "\n".join(s.status_line() for s in self.strategies) + "\n"
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
    @staticmethod
    def _calc_atr(bars: list, period: int) -> float | None:
        if len(bars) <= period:
            return None
        trs = []
        for prev, cur in zip(bars, bars[1:]):
            trs.append(max(cur.high - cur.low, abs(cur.high - prev.close),
                           abs(cur.low - prev.close)))
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = atr * (period - 1) / period + tr / period
        return atr

    @staticmethod
    def _to_bars(candles: list, tf: int) -> list:
        """M5 sviečky → bary požadovaného timeframu (zoskupené na hranice)."""
        buckets: dict[int, Bar] = {}
        for c in candles:
            k = int(c["time"] // tf)
            b = buckets.get(k)
            if b is None:
                buckets[k] = Bar(k * tf, c["o"], c["h"], c["l"], c["c"])
            else:
                b.high = max(b.high, c["h"])
                b.low = min(b.low, c["l"])
                b.close = c["c"]
        return [buckets[k] for k in sorted(buckets)]

    def _bootstrap_atr(self) -> None:
        """ATR pre každý použitý timeframe + predohriatie stavových stratégií."""
        try:
            candles = self.broker.candles_m5(3000)      # ~10 dní
        except CTraderError as exc:
            log.warning("Bootstrap zlyhal (%s).", exc)
            return
        if not candles:
            return
        for tf in self._tfs:
            bars = self._to_bars(candles, tf)
            if len(bars) > 1:
                bars = bars[:-1]                        # posledný bar je neúplný
            self._atr[tf] = self._calc_atr(bars, self.cfg.ATR_PERIOD)
            self._atr_prev[tf] = bars[-1].close if bars else None
            for s in self.strategies:
                if s.enabled and s.timeframe_s == tf:
                    s.warmup(bars)
            log.info("TF %ds: ATR %.6f, %d barov (warmup hotový).",
                     tf, self._atr[tf] or 0.0, len(bars))

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
        still = self.db.open_trades()
        for s in self.strategies:
            s.restore([r for r in still if r["strategy"] == s.id])
        saved = self.db.meta_get(f"ref:{self.strategy.id}", "")
        if saved and "|" in saved:
            rl, rs = (float(x) for x in saved.split("|"))
            if rl > 0:
                self.strategy.ref_long = rl
            if rs > 0:
                self.strategy.ref_short = rs
            log.info("Kotvy obnovené z DB: ref_L=%.5f ref_S=%.5f", rl, rs)
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
        self._enforce_time_stops()
        self._daily_snapshot()
        self.macro.refresh()
        if self.commands_enabled:
            self.tg.poll_commands(self._handle_command)
            self.db.meta_set("tg_offset", self.tg.offset)

    def _price(self) -> dict | None:
        q = self.broker.quote()
        if q is None or q["age_s"] > 120 or not self.broker.is_connected():
            self._maybe_gap_alarm()
            self._maybe_reconnect()
            return None
        self.last_md_ts = time.time()
        if self._gap_alarmed:
            self._gap_alarmed = False
            self.auto_paused = False
            self.tg.send("✅ Stream znovu beží, pauza zrušená.")
        return q

    def _maybe_reconnect(self) -> None:
        """SDK (Twisted ClientService) sa reconnectuje samo a auth reťazec
        beží v callbackoch — do toho NEZASAHUJEME, dva klienti naraz spôsobia
        auth timeouty. Ak je stream mŕtvy dlhšie než HARD_RESTART_S, spravíme
        tvrdý reštart procesu (wrapper zdvihne čistú inštanciu)."""
        if self.broker.is_connected():
            return
        dead_s = time.time() - self.last_md_ts
        if dead_s < self.cfg.HARD_RESTART_S:
            return
        log.warning("Stream mŕtvy %.0f min — tvrdý reštart procesu.", dead_s / 60)
        self.tg.send(f"♻️ Spojenie mŕtve > {int(dead_s // 60)} min napriek "
                     f"auto-reconnectu — reštartujem proces.")
        self.db.log_event("warn", "stream mŕtvy, reštart procesu")
        import os as _os
        _os._exit(1)

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
        """Buduje bary pre každý použitý timeframe a po uzavretí baru
        zavolá stratégie, ktoré na danom TF bežia."""
        now = time.time()
        for tf in self._tfs:
            st = self._bars.setdefault(tf, {"bucket": None, "bar": None})
            bucket = int(now // tf)
            if st["bucket"] is None:
                st["bucket"] = bucket
                st["bar"] = Bar(bucket * tf, mid, mid, mid, mid)
                continue
            if bucket == st["bucket"]:
                b = st["bar"]
                b.high = max(b.high, mid)
                b.low = min(b.low, mid)
                b.close = mid
                continue

            closed = st["bar"]
            st["bucket"] = bucket
            st["bar"] = Bar(bucket * tf, mid, mid, mid, mid)

            n = self.cfg.ATR_PERIOD
            pc = self._atr_prev.get(tf) or closed.open
            tr = max(closed.high - closed.low, abs(closed.high - pc),
                     abs(closed.low - pc))
            prev_atr = self._atr.get(tf)
            self._atr[tf] = tr if prev_atr is None else prev_atr * (n - 1) / n + tr / n
            self._atr_prev[tf] = closed.close

            for s in self.strategies:
                if not s.enabled or s.timeframe_s != tf:
                    continue
                for sig in s.on_bar(closed, self._atr[tf]):
                    self._execute(sig, closed)
                if hasattr(s, "ref_long"):
                    self.db.meta_set(f"ref:{s.id}",
                                     f"{s.ref_long or 0}|{s.ref_short or 0}")

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
                               0.0, 0.0, "blocked", reason, sig.context)
            return
        units = sig.qty if sig.side == "long" else -sig.qty
        try:
            res = self.broker.market_order_with_tp(units, sig.tp_price,
                                                   tag=sig.strategy_id,
                                                   sl_price=sig.sl_price)
        except CTraderError as exc:
            self.db.log_signal(sig.strategy_id, sig.side, bar.close,
                               0.0, 0.0, "error", str(exc), sig.context)
            log.warning("Vstup zlyhal: %s", exc)
            return
        ctx = dict(sig.context)
        ctx.update({"reason": sig.reason, "failsafe": self.failsafe,
                    "sl_price": sig.sl_price, "max_hold_s": sig.max_hold_s})
        trade_id = self.db.open_trade(
            sig.strategy_id, sig.side, sig.qty, res["price"], sig.tp_price,
            res["position_id"], res["order_id"], 0.0, ctx)
        for s in self.strategies:
            if s.id == sig.strategy_id:
                s.on_trade_opened(trade_id, sig.side, res["price"])
        self.db.log_signal(sig.strategy_id, sig.side, bar.close,
                           0.0, 0.0, "executed", sig.reason, ctx)
        sl_txt = f" | SL {sig.sl_price:.5f}" if sig.sl_price else ""
        self.tg.send(f"📈 <b>{sig.strategy_id}</b> OTVORENÉ {sig.side.upper()} "
                     f"{sig.qty:,.0f} {self.cfg.SYMBOL} @ {res['price']:.5f}\n"
                     f"TP {sig.tp_price:.5f}{sl_txt} (na serveri)\n"
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

    def _enforce_time_stops(self) -> None:
        """Zatvorí pozície, ktoré prekročili max_hold_s (S7 má 24 h)."""
        now = time.time()
        for row in self.db.open_trades():
            try:
                ctx = json.loads(row["context"] or "{}")
            except ValueError:
                continue
            mh = float(ctx.get("max_hold_s") or 0)
            if not mh or now - row["ts_open"] <= mh:
                continue
            log.info("Časový stop: zatváram #%d (%s, %.1f h).",
                     row["id"], row["strategy"], (now - row["ts_open"]) / 3600)
            try:
                self.broker.close_trade(str(row["entry_order_id"]))
                self.tg.send(f"⏱ <b>{row['strategy']}</b> časový stop "
                             f"({mh / 3600:.0f} h) — zatváram #{row['id']}.")
            except CTraderError as exc:
                log.warning("Časový stop zlyhal pre #%d: %s", row["id"], exc)

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
        for s in self.strategies:
            if s.id == row["strategy"]:
                s.on_trade_closed(db_id, row["side"], close_price)
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
                         + "\n".join(s.status_line() for s in self.strategies))
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
