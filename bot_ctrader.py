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
import html
import json
import logging
import os
import queue
import sys
import threading
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
from trading.shadow_judge import ShadowJudge
from trading.sync_supabase import SupabaseSync
from trading.tg import Telegram

# Daily Plan executor beží v procese bota — Spotware dovolí jedno
# app-auth spojenie. Import je lenivý až v __init__, aby prípadná chyba
# modulu nezhodila grid pri štarte.

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("bot_ctrader")

# Sviečky pre Daily Plan builder — bot ich sype, plánovač číta.
PLAN_CANDLES = ROOT / "data" / "plan_candles.json"


def _iso_utc(ts: float | None) -> str | None:
    """Unix čas → ISO 8601 v UTC pre Supabase (timestamptz)."""
    if not ts:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


@dataclass
class CTraderBotConfig:
    DEMO: bool = True              # natvrdo; live = vedomá zmena + env potvrdenie
    SYMBOL: str = "EURUSD"
    QTY: float = 10_000
    CAP_BASE: int = 20             # lab G2B kapacita 20+10
    CAP_RESERVE: int = 10
    STEP_SHORT: float = 0.0015     # lab G2B geometria
    STEP_LONG: float = 0.00225
    P500_SIGNALS: bool = True      # zrkadliace signály pre Plus500 (TG)
    P500_SIGNAL_QTY: float = 10_000
    BRIEFING_HOUR: int = 8         # ranný briefing 8–10 h
    BRIEFING_HOUR_END: int = 10
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
        # getUpdates je exkluzívny — dvaja odberatelia si updaty kradnú. Kým
        # bežal IBKR bot na tom istom tokene, príkazy tu museli byť vypnuté.
        # IBKR je vyradený (29. 7.), takže zdieľaný token stačí; vlastný token
        # má prednosť a CTRADER_TG_COMMANDS=0 je vypínač, ak by IBKR ožil.
        self.commands_enabled = (self.tg.enabled
                                 and os.getenv("CTRADER_TG_COMMANDS", "1") != "0")
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
                                   reserve_levels=cfg.CAP_RESERVE,
                                   step_short=cfg.STEP_SHORT,
                                   step_long=cfg.STEP_LONG))
        grid.id = "Grid25-G2B-CT"
        s7 = S7Continuation(S7Config(qty=cfg.S7_QTY))
        s7.enabled = cfg.S7_ENABLED
        self.strategies = [grid, s7]
        self.strategy = grid          # spätná kompatibilita (kotvy, status)
        self.judge = ShadowJudge(cfg.DB_PATH,
                                 os.getenv("ANTHROPIC_API_KEY", ""))
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
        self._brief_date = self.db.meta_get("brief_date", "")
        self._stop = threading.Event()
        self._cmd_queue: queue.Queue = queue.Queue()
        self._saved_offset = self.tg.offset
        # Supabase zrkadlo pre dashboard. SQLite spojenie je viazané na
        # vlákno, ktoré ho vytvorilo, takže snapshot staviame TU (obchodné
        # vlákno) a push vlákno už len posiela hotový dict.
        self.sync = SupabaseSync()
        self._sync_snap: dict = {}
        self._sync_lock = threading.Lock()
        self._sb_queue: queue.Queue = queue.Queue()
        # Balance je sieťové volanie na brokera — snapshot beží každý tick
        # (10 s), takže hodnotu cacheujeme a obnovujeme raz za 5 minút.
        self._last_balance: float = 0.0
        self._balance_ts: float = 0.0
        # Daily Plan runner — schválené plány vykonáva vnútri procesu bota.
        try:
            from daily_plan.runtime import DailyPlanRunner
            self.plan_runner = DailyPlanRunner(self.broker, self.sync, self.tg)
        except Exception:  # noqa: BLE001 — Daily Plan nesmie blokovať grid
            log.exception("Daily Plan runner sa nepodarilo spustiť")
            self.plan_runner = None
        self._capital: dict = {}
        self._margin_cache: dict = {}
        self._margin_ts: float = 0.0
        self._capital_ts: float = 0.0
        self._calendar_ts: float = 0.0
        self._candles_ts: float = 0.0

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
                     f"Balance: {acct['balance']:,.2f}\n"
                     + self._config_line())
        self.db.log_event("info", "ctrader bot štart")
        self._start_command_thread()
        self._last_balance = acct["balance"]
        self._balance_ts = time.time()
        self._refresh_sync_snapshot(acct["balance"])
        self.sync.start(snapshot=self._sync_snapshot,
                        on_command=self._sb_queue.put,
                        on_pause=self._sb_pause)

        deadline = time.time() + run_minutes * 60 if run_minutes else None
        try:
            while True:
                self._check_suspend()
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
            self._stop.set()
            self.sync.stop()
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
            if tf == self._tfs[0]:
                try:
                    self.judge.bootstrap_candles(bars)
                except Exception:  # noqa: BLE001
                    log.exception("ShadowJudge bootstrap zlyhal")
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
        # kotvy obnov VŽDY (aj bez otvorených obchodov — bug: predtým sa
        # preskočili early-returnom a status ukazoval ref 0.00000)
        saved = self.db.meta_get(f"ref:{self.strategy.id}", "")
        if saved and "|" in saved:
            rl, rs = (float(x) for x in saved.split("|"))
            if rl > 0:
                self.strategy.ref_long = rl
            if rs > 0:
                self.strategy.ref_short = rs
            log.info("Kotvy obnovené z DB: ref_L=%.5f ref_S=%.5f", rl, rs)

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
            self.judge.on_price(px["mid"])
            self._update_failsafe_daily(px["mid"])
            self._aggregate_bar(px["mid"])
        self._poll_closes()
        self._enforce_time_stops()
        self._morning_briefing(px["mid"] if px else None)
        self._daily_snapshot()
        self.macro.refresh()
        self._drain_commands()
        self._drain_sb_commands()
        self._refresh_sync_snapshot()
        self._push_calendar()
        self._dump_plan_candles()
        if self.plan_runner is not None:
            try:
                self.plan_runner.tick(px["mid"] if px else None)
            except Exception:  # noqa: BLE001
                log.exception("Daily Plan tick zlyhal")

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

    # --- Telegram príkazy ---------------------------------------------------
    def _start_command_thread(self) -> None:
        """Polling beží vo vlastnom vlákne, aby ho obchodná slučka nemohla
        zablokovať (broker volania majú timeout 15–20 s). Vlákno len číta
        frontu; /pauza a /start (atomický flag) vybaví hneď, ostatné odovzdá
        obchodnému vláknu — tam sa číta DB a stav stratégií, takže sa nič
        nemutuje z dvoch vlákien naraz."""
        if not self.commands_enabled:
            log.info("TG príkazy vypnuté (CTRADER_TG_COMMANDS=0).")
            return

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.tg.poll_commands(self._on_command_async, timeout=25)
                except Exception:  # noqa: BLE001 — vlákno musí prežiť všetko
                    log.exception("Chyba v polling vlákne príkazov")
                    self._stop.wait(10)

        threading.Thread(target=loop, name="tg-commands", daemon=True).start()
        log.info("TG príkazy zapnuté (polling vo vlastnom vlákne).")

    def _on_command_async(self, cmd: str, args: str) -> None:
        """Beží v polling vlákne. /pauza a /start menia jediný float
        (paused_until), takže ich vybavíme hneď — musia fungovať aj keď
        obchodná slučka viazne. Zvyšok číta DB a stav stratégií → do fronty
        pre obchodné vlákno."""
        if cmd in ("/pauza", "/start"):
            self._handle_command(cmd, args)
            return
        self._cmd_queue.put((cmd, args))

    def _drain_commands(self) -> None:
        """Beží v obchodnom vlákne (z _tick)."""
        while True:
            try:
                cmd, args = self._cmd_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_command(cmd, args)
            except Exception as exc:  # noqa: BLE001
                log.exception("Chyba pri spracovaní príkazu %s", cmd)
                self.tg.send(f"⚠️ Chyba príkazu {cmd}: {exc}")
        if self.commands_enabled and self.tg.offset != self._saved_offset:
            self._saved_offset = self.tg.offset
            self.db.meta_set("tg_offset", self.tg.offset)

    def _check_suspend(self) -> None:
        """Zachytí uspatie stroja (macOS sleep). Slučka tiká každých
        TICK_SECONDS; skok o rád viac znamená, že proces bol zmrazený a
        TCP spojenie je po prebudení mŕtve, aj keď to socket ešte nevie.
        Bez tohto sa čaká 3 min na re-subscribe a 15 min na tvrdý reštart —
        po prebudení zbytočne dlho. Reconnectneme hneď a časovače nulujeme,
        aby uspatie nespustilo falošný alarm mŕtveho streamu."""
        now = time.time()
        prev = getattr(self, "_last_loop_ts", None)
        self._last_loop_ts = now
        gap = now - prev if prev else 0.0
        if gap < max(self.cfg.TICK_SECONDS * 6, 60.0):
            return
        log.warning("Slučka stála %.0f s (uspatie stroja?) — obnovujem "
                    "spojenie.", gap)
        self.db.log_event("warn", f"proces zmrazený {gap / 60:.0f} min "
                                  f"(uspatie stroja), reconnect")
        try:
            self.broker.reconnect()
            log.info("Spojenie obnovené po prebudení.")
        except Exception as exc:  # noqa: BLE001
            log.warning("Reconnect po prebudení zlyhal: %s", exc)
        self.last_md_ts = time.time()
        self._last_resub = 0.0

    @staticmethod
    def _market_closed(now: float | None = None) -> bool:
        """Je forexový trh zatvorený?

        Týždeň beží od nedeľného otvorenia Sydney (21:00 UTC) do piatkového
        zatvorenia New Yorku (21:00 UTC). Cez víkend nechodia žiadne ticky —
        watchdog to bez tejto kontroly čítal ako mŕtvy stream a reštartoval
        proces každých 15 minút celý víkend (1. 8. 2026: 31 reštartov do
        soboty predpoludním, každý s Telegram hláškou).
        """
        t = datetime.fromtimestamp(now or time.time(), timezone.utc)
        wd, hour = t.weekday(), t.hour        # pondelok = 0, nedeľa = 6
        if wd == 5:                            # sobota celá
            return True
        if wd == 4 and hour >= 21:             # piatok po 21:00 UTC
            return True
        if wd == 6 and hour < 21:              # nedeľa do 21:00 UTC
            return True
        return False

    def _maybe_reconnect(self) -> None:
        """Trojstupňová obrana mŕtveho streamu:
        1. spojenie žije, ale spoty nechodia > 3 min → re-subscribe (lacné;
           subscription vie umrieť aj pri živom TCP)
        2. SDK sa reconnectuje samo (auth reťazec beží v callbackoch)
        3. dáta mŕtve > HARD_RESTART_S bez ohľadu na stav spojenia →
           tvrdý reštart procesu (wrapper zdvihne čistú inštanciu)"""
        now = time.time()
        dead_s = now - self.last_md_ts

        # Cez víkend ticky nechodia zo svojej podstaty — ani
        # re-subscribe, ani tvrdý reštart nič nevyriešia.
        if self._market_closed(now):
            return

        if self.broker.is_connected() and dead_s > 180 \
                and now - getattr(self, "_last_resub", 0) > 120:
            self._last_resub = now
            try:
                self.broker._subscribe_spots()
                log.info("Stream stojí %.0f s pri živom spojení — poslal "
                         "som re-subscribe.", dead_s)
            except Exception as exc:  # noqa: BLE001
                log.warning("Re-subscribe zlyhal: %s", exc)

        if dead_s < self.cfg.HARD_RESTART_S:
            return
        log.warning("Stream mŕtvy %.0f min — tvrdý reštart procesu.", dead_s / 60)
        self.tg.send(f"♻️ Dáta mŕtve > {int(dead_s // 60)} min napriek "
                     f"obrane — reštartujem proces.")
        self.db.log_event("warn", "stream mŕtvy, reštart procesu")
        import os as _os
        _os._exit(1)

    def _maybe_gap_alarm(self) -> None:
        stale = time.time() - self.last_md_ts
        if self._market_closed():
            return
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
                    self._execute(sig, closed, self._atr[tf])
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

    def _execute(self, sig: Signal, bar: Bar,
                 atr: float | None = None) -> None:
        reason = self._blocked_reason()
        open_rows = self.db.open_trades()
        # ATR aj floating sa sem posielali natvrdo ako 0.0. Sudca to
        # správne označil za nemožnú hodnotu a vetoval každý signál —
        # 30 z 30. Posielame skutočné čísla, ktoré bot aj tak má.
        q = self.broker.quote()
        mid = q["mid"] if q else None
        floating = 0.0
        if mid is not None:
            for r in open_rows:
                floating += ((mid - r["entry_price"]) * r["qty"]
                             if r["side"] == "long"
                             else (r["entry_price"] - mid) * r["qty"])
        jid = self.judge.submit(
            sig.strategy_id, sig.side, bar.close, sig.tp_price,
            getattr(sig, "sl_price", 0.0), atr or 0.0,
            {"long": sum(1 for r in open_rows if r["side"] == "long"),
             "short": sum(1 for r in open_rows if r["side"] == "short")},
            floating,
            [f"{datetime.fromtimestamp(e['ts'], self.tz):%H:%M} "
             f"{e['currency']} {e['title']}"
             for e in self.macro.todays_events(self.tz)],
            blocked=reason or "")
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
        # Spread pri vstupe je jediný nákladový komponent, ktorý sa nikde
        # neúčtuje samostatne (je zapečený v cene) — bez neho sa v dashboarde
        # nedá rozpísať náklad na províziu/spread/funding.
        q_entry = self.broker.quote()
        if q_entry:
            ctx["spread_at_entry"] = round(q_entry["spread"], 6)
        trade_id = self.db.open_trade(
            sig.strategy_id, sig.side, sig.qty, res["price"], sig.tp_price,
            res["position_id"], res["order_id"], 0.0, ctx)
        self.judge.link(jid, trade_id)
        self._refresh_sync_snapshot()
        self.sync.trigger()
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
        if self.cfg.P500_SIGNALS:
            self.tg.send(self._p500_open_msg(trade_id, sig.side, res["price"],
                                             sig.tp_price, sig.sl_price))
        log.info("OTVORENÉ %s @ %.5f (pozícia %s, db #%d)",
                 sig.side, res["price"], res["position_id"], trade_id)

    def _p500_open_msg(self, trade_id: int, side: str, entry: float,
                       tp: float, sl: float = 0.0) -> str:
        q = self.cfg.P500_SIGNAL_QTY
        smer = "🔺 <b>KÚPIŤ</b>" if side == "long" else "🔻 <b>PREDAŤ</b>"
        zisk = abs(tp - entry) * q / tp
        sl_line = (f"🛑 Zavrieť pri strate: <code>{sl:.5f}</code>\n"
                   if sl else "🚫 Stop Loss: nenastavuj\n")
        return (f"🟠 <b>P500 SIGNÁL #{trade_id} — OTVOR</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"{smer} EUR/USD\n"
                f"Čiastka: <b>{q:,.0f}</b>\n"
                f"Trhová cena teraz: ~<code>{entry:.5f}</code>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"Po otvorení nastav:\n"
                f"🎯 Zavrieť pri zisku: <code>{tp:.5f}</code>\n"
                f"{sl_line}"
                f"Očakávaný zisk pri cieli: ~{zisk:.2f} €")

    def _p500_close_msg(self, trade_id: int, side: str, entry: float,
                        exit_price: float) -> str:
        q = self.cfg.P500_SIGNAL_QTY
        zisk = (exit_price - entry) * q / exit_price if side == "long" \
            else (entry - exit_price) * q / exit_price
        return (f"🟢 <b>P500 SIGNÁL #{trade_id} — ZATVORENÉ</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"{side.upper()} z <code>{entry:.5f}</code> skončil na "
                f"<code>{exit_price:.5f}</code>.\n"
                f"Ak máš nastavené „Zavrieť pri zisku/strate“, pozícia sa "
                f"zatvorila sama — skontroluj v appke.\n"
                f"Očakávaný výsledok: {zisk:+.2f} € na {q:,.0f}")

    def _config_line(self) -> str:
        g = self.strategy.cfg
        return (f"⚙️ G2B {g.qty:,.0f} | krok +{g.step_short:.2%}/−{g.step_long:.3%} "
                f"| TP {g.tp_pct:.2%} | pásma {g.band_low:.2f}–{g.band_high:.2f} "
                f"| kapacita {g.base_levels}+{g.reserve_levels} | blackout ±30 min "
                f"| G8 poistka | S7 {'ON' if self.cfg.S7_ENABLED else 'off'}")

    def _morning_briefing(self, mid: float | None) -> None:
        now = datetime.now(self.tz)
        today = now.strftime("%Y-%m-%d")
        if (not self.cfg.BRIEFING_HOUR <= now.hour < self.cfg.BRIEFING_HOUR_END
                or self._brief_date == today):
            return
        self._brief_date = today
        self.db.meta_set("brief_date", today)
        try:
            bal = self.broker.account_summary()["balance"]
        except CTraderError:
            bal = 0.0
        rows = self.db.open_trades()
        floating = 0.0
        if mid:
            for r in rows:
                floating += ((mid - r["entry_price"]) if r["side"] == "long"
                             else (r["entry_price"] - mid)) * r["qty"]
        from datetime import timedelta
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        cycles = self.db.cycles_on_day(yesterday)
        events = self.macro.todays_events(self.tz)
        ev = "\n".join(f"  • {datetime.fromtimestamp(e['ts'], self.tz):%H:%M} "
                        f"{e['currency']} {e['title']}" for e in events) \
            or "  (žiadne)"
        longs = sum(1 for r in rows if r["side"] == "long")
        self.tg.send(f"☀️ <b>Ranný briefing</b> {now:%d.%m.%Y}\n"
                     f"Balance: {bal:,.2f} € | floating {floating:+,.2f}\n"
                     f"Pozície: {len(rows)} (long {longs} / short "
                     f"{len(rows) - longs})\n"
                     + (f"Kurz: {mid:.5f}\n" if mid else "")
                     + f"Včerajšie cykly: {cycles}\n"
                     + self._config_line() + "\n"
                     f"Dnešné high-impact udalosti:\n{ev}")

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
                # Bola tu neexistujúca broker.close_trade() — časový stop by
                # spadol, keby ho niekedy stratégia zapla (max_hold_s > 0).
                self.broker.close_position(row["entry_order_id"])
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
        if self.cfg.P500_SIGNALS:
            self.tg.send(self._p500_close_msg(db_id, row["side"],
                                              row["entry_price"], close_price))
        log.info("ZAVRETÉ db #%d %s @ %.5f, P/L %+.2f%s",
                 db_id, row["side"], close_price, pnl, note)
        self._refresh_sync_snapshot()
        self.sync.trigger()

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
        elif cmd in ("/help", "/pomoc"):
            self.tg.send(self._commands_help())
        else:
            # Bez tejto vetvy bot na preklep ticho mlčal a vyzeralo to ako
            # výpadok pollingu. Príkaz ide od používateľa a správy chodia
            # s parse_mode=HTML, takže ho treba escapovať — inak by "/<b"
            # zhodilo sendMessage na chybe 400 a bot by mlčal znova.
            self.tg.send(f"❓ Neznámy príkaz <code>{html.escape(cmd)}</code>.\n\n"
                         + self._commands_help())

    # --- Supabase zrkadlo a príkazy z dashboardu -------------------------
    def _capital_cached(self, max_age_s: float = 6 * 3600) -> dict:
        """Vklady/výbery a swap sadzby. Oboje sa mení zriedka, ale bez nich
        sa nedá spočítať návratnosť ani predpovedať náklad držania.

        Cash flow je 17 dotazov (týždenné okná), preto raz za 6 hodín.
        """
        if time.time() - self._capital_ts <= max_age_s and self._capital:
            return self._capital
        try:
            flows = self.broker.cash_flow(days=120)
            sym = self.broker.symbol_details()
            self._capital = {
                "deposits_net": sum(f["delta"] for f in flows),
                "deposits_count": len(flows),
                "swap_long": sym["swap_long"],
                "swap_short": sym["swap_short"],
                "swap_rollover_3days": sym["swap_rollover_3days"],
            }
            self._capital_ts = time.time()
            self._check_swap_drift(self._capital)
        except CTraderError as exc:
            log.warning("Kapitál/swap sa nepodarilo načítať: %s", exc)
        return self._capital

    # Prahy pre alarm na zmenu swapu. Relatívny chytí posun sadzby,
    # absolútny chytí prípad, keď bola sadzba blízko nuly — tam by
    # percento nikdy nevystrelilo, hoci lacná strana práve zdražela.
    SWAP_REL = 0.25
    SWAP_ABS = 0.15

    def _check_swap_drift(self, cap: dict) -> None:
        """Ustráži úrokový diferenciál.

        Grid stojí na tom, že jedna strana je lacná — na EURUSD platí short
        23× menej než long. Keby broker sadzby prehodil, náklad držania by
        vyskočil a zistili by sme to až z účtovania o dni neskôr.
        """
        cur_l = cap.get("swap_long")
        cur_s = cap.get("swap_short")
        if cur_l is None or cur_s is None:
            return
        prev = self.db.meta_get("swap_rates", "")
        self.db.meta_set("swap_rates", f"{cur_l}|{cur_s}")
        if not prev:
            return                      # prvý beh — nie je s čím porovnať
        try:
            old_l, old_s = (float(x) for x in prev.split("|"))
        except ValueError:
            return
        if (old_l, old_s) == (cur_l, cur_s):
            return

        moved = []
        for name, old, cur in (("long", old_l, cur_l), ("short", old_s, cur_s)):
            delta = cur - old
            if abs(delta) < self.SWAP_ABS and abs(delta) < abs(old) * self.SWAP_REL:
                continue
            moved.append(f"{name} {old:+.4f} → {cur:+.4f} ({delta:+.4f})")
        if not moved:
            return

        # Prehodenie lacnej strany je vážnejšie než samotná zmena sadzby:
        # mriežka je postavená na tom, ktorá strana je lacná.
        old_cheap = "short" if abs(old_s) < abs(old_l) else "long"
        new_cheap = "short" if abs(cur_s) < abs(cur_l) else "long"
        flip = old_cheap != new_cheap
        sign = (old_l <= 0 < cur_l) or (old_s <= 0 < cur_s)

        head = "🔄 Swap sadzby sa zmenili"
        if flip:
            head = "🚨 Swap: PREHODILA SA LACNÁ STRANA"
        elif sign:
            head = "💰 Swap: jedna strana začala platiť TEBE"

        msg = (f"{head} ({self.cfg.SYMBOL})\n" + "\n".join(moved))
        if flip:
            msg += (f"\nLacná strana bola <b>{old_cheap}</b>, teraz je "
                    f"<b>{new_cheap}</b> — mriežka drží prevažne "
                    f"{old_cheap}y.")
        self.tg.send(msg)
        self.db.log_event("alarm" if flip else "info",
                          f"swap zmena: {'; '.join(moved)}")
        log.warning("Swap sadzby sa zmenili: %s", "; ".join(moved))

    def _margin_now(self, equity: float, max_age_s: float = 60.0) -> dict:
        """Využitie marže. ProtoOATrader maržu nenesie, takže ju skladáme
        z otvorených pozícií (reconcile vracia usedMargin per pozícia).

        Cachujeme: snapshot sa stavia každý tick (10 s) a bez cache to
        znamenalo reconcile request na brokera šesťkrát za minútu.
        """
        if (time.time() - self._margin_ts <= max_age_s
                and self._margin_cache):
            used = self._margin_cache["used_margin"]
            return {"used_margin": used, "free_margin": equity - used,
                    "margin_level": (equity / used * 100) if used else None}
        try:
            plist = self.broker.positions()
            used = sum(p.get("used_margin", 0.0) for p in plist)
        except CTraderError as exc:
            log.debug("Maržu sa nepodarilo zistiť: %s", exc)
            return self._margin_cache or {}
        self._margin_ts = time.time()
        self._margin_cache = {"used_margin": used, "positions": plist}
        return {
            "used_margin": used,
            "free_margin": equity - used,
            "margin_level": (equity / used * 100) if used else None,
        }

    def _dump_plan_candles(self, max_age_s: float = 6 * 3600) -> None:
        """Odloží H1 a D1 sviečky na disk pre Daily Plan builder.

        Spotware demo dovolí len jedno app-auth spojenie naraz, takže si
        plánovač nemôže otvoriť vlastné. Bot ich preto raz za pol dňa
        vysype do súboru a plánovač ich číta odtiaľ — jedno spojenie,
        voľná väzba a pád plánovača nezhodí grid.
        """
        if not self.sync.enabled and not PLAN_CANDLES.parent.exists():
            return
        if time.time() - self._candles_ts <= max_age_s:
            return
        self._candles_ts = time.time()
        try:
            data = {
                "fetched_at": _iso_utc(time.time()),
                "symbol": self.cfg.SYMBOL,
                "h1": self.broker.candles("H1", 2000),
                "d1": self.broker.candles("D1", 500),
            }
        except CTraderError as exc:
            log.warning("Dump sviečok pre Daily Plan zlyhal: %s", exc)
            return
        try:
            PLAN_CANDLES.parent.mkdir(parents=True, exist_ok=True)
            tmp = PLAN_CANDLES.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(PLAN_CANDLES)      # atomicky, aby plánovač nečítal polovicu
            log.info("Sviečky pre Daily Plan uložené (H1 %d, D1 %d).",
                     len(data["h1"]), len(data["d1"]))
        except OSError as exc:
            log.warning("Zápis sviečok zlyhal: %s", exc)

    def _push_calendar(self) -> None:
        """Nahrá high-impact udalosti do Supabase (raz za hodinu)."""
        if not self.sync.enabled:
            return
        if time.time() - self._calendar_ts < 3600:
            return
        self._calendar_ts = time.time()
        rows = [{
            "ts": _iso_utc(e["ts"]),
            "currency": e["currency"],
            "title": e["title"],
            "impact": e.get("impact", "high"),
        } for e in self.macro.events]
        if rows:
            self.sync.push_calendar(rows)

    def _balance_cached(self, max_age_s: float = 300.0) -> float:
        if time.time() - self._balance_ts > max_age_s:
            try:
                self._last_balance = self.broker.account_summary()["balance"]
                self._balance_ts = time.time()
            except CTraderError as exc:
                log.debug("Balance sa nepodarilo obnoviť: %s", exc)
        return self._last_balance

    def _sync_snapshot(self) -> dict:
        """Beží v push vlákne — vracia len to, čo pripravilo obchodné."""
        with self._sync_lock:
            return dict(self._sync_snap)

    def _refresh_sync_snapshot(self, balance: float | None = None) -> None:
        """Beží v obchodnom vlákne (SQLite spojenie je viazané naň)."""
        if not self.sync.enabled:
            return
        try:
            q = self.broker.quote()
            mid = q["mid"] if q else None
            rows = self.db.open_trades()
            # Broker hlási naakumulovaný swap pre otvorené pozície priebežne
            # (reconcile); grid DB ho má až pri zavretí. Bez tohto boli
            # náklady držania v dashboarde nulové.
            swap_map = {bp["position_id"]: bp.get("swap", 0.0)
                        for bp in (self._margin_cache.get("positions") or [])}
            positions, floating = [], 0.0
            for r in rows:
                ctx = json.loads(r["context"] or "{}")
                spread = ctx.get("spread_at_entry")
                pnl = None
                if mid is not None:
                    pnl = (mid - r["entry_price"]) * r["qty"] \
                        if r["side"] == "long" \
                        else (r["entry_price"] - mid) * r["qty"]
                    floating += pnl
                positions.append({
                    "id": r["id"],
                    "broker_position_id": r["entry_order_id"],
                    "strategy": r["strategy"],
                    "symbol": self.cfg.SYMBOL,
                    "side": r["side"],
                    "qty": r["qty"],
                    "entry_price": r["entry_price"],
                    "tp_price": r["tp_price"],
                    "opened_at": _iso_utc(r["ts_open"]),
                    "funding_usd": swap_map.get(r["entry_order_id"],
                                                r["funding_usd"]),
                    "commission_usd": r["commission_usd"],
                    "pnl_float": pnl,
                    "spread_at_entry": spread,
                    "spread_cost_usd": (spread * r["qty"])
                                       if spread is not None else None,
                    "context": ctx,
                    "updated_at": _iso_utc(time.time()),
                })

            # Zavreté obchody posielame prírastkovo — celá história každú
            # minútu je zbytočná záťaž, ale prekryv 1 h je poistka proti
            # obchodu, ktorý sa dopísal tesne po poslednom pushi.
            since = float(self.db.meta_get("sb_trades_until", "0") or 0)
            closed = self.db.conn.execute(
                "SELECT * FROM trades WHERE status='closed' AND ts_close >= ? "
                "ORDER BY ts_close", (max(since - 3600, 0),)).fetchall()
            trades, newest = [], since
            for r in closed:
                ctx = json.loads(r["context"] or "{}")
                spread = ctx.get("spread_at_entry")
                gross = r["pnl_usd"] or 0.0
                comm = r["commission_usd"] or 0.0
                fund = r["funding_usd"] or 0.0
                trades.append({
                    "id": r["id"],
                    "broker_position_id": r["entry_order_id"],
                    "strategy": r["strategy"],
                    "symbol": self.cfg.SYMBOL,
                    "side": r["side"],
                    "qty": r["qty"],
                    "entry_price": r["entry_price"],
                    "tp_price": r["tp_price"],
                    "close_price": r["close_price"],
                    "opened_at": _iso_utc(r["ts_open"]),
                    "closed_at": _iso_utc(r["ts_close"]),
                    "gross_pnl_usd": gross,
                    "pnl_usd": gross - comm + fund,
                    "commission_usd": comm,
                    "funding_usd": fund,
                    "spread_at_entry": spread,
                    "spread_cost_usd": (spread * r["qty"])
                                       if spread is not None else None,
                    "manual_close": bool(r["manual_close"]),
                    "context": ctx,
                })
                newest = max(newest, r["ts_close"] or 0)
            # Značku posúvame až podľa toho, čo Supabase potvrdila. Keby sa
            # posúvala tu, každý výpadok siete by tichom preskočil obchody,
            # ktoré sa medzitým zavreli — presne to sa stalo pri 401 na
            # zlom kľúči a história sa nikdy neodoslala.
            confirmed = self.sync.confirmed_trades_until
            if confirmed > since:
                self.db.meta_set("sb_trades_until", confirmed)

            daily = [{
                "day": d["day"], "strategy": d["strategy"],
                "cycles": d["cycles"], "pnl_usd": d["pnl"],
                "commission_usd": d["comm"], "funding_usd": d["fund"],
                "wins": d["wins"], "losses": d["losses"],
            } for d in self.db.conn.execute(
                "SELECT date(ts_close,'unixepoch') day, strategy, "
                "COUNT(*) cycles, SUM(pnl_usd-commission_usd+funding_usd) pnl, "
                "SUM(commission_usd) comm, SUM(funding_usd) fund, "
                "SUM(CASE WHEN pnl_usd-commission_usd+funding_usd > 0 "
                "THEN 1 ELSE 0 END) wins, "
                "SUM(CASE WHEN pnl_usd-commission_usd+funding_usd <= 0 "
                "THEN 1 ELSE 0 END) losses "
                "FROM trades WHERE status='closed' AND ts_close IS NOT NULL "
                "GROUP BY day, strategy ORDER BY day DESC LIMIT 120")]

            bal = balance if balance is not None else self._balance_cached()
            reason = self._blocked_reason()
            equity = (bal or 0.0) + floating
            cap = self._capital_cached()
            margin = self._margin_now(equity) if positions else {
                "used_margin": 0.0, "free_margin": equity,
                "margin_level": None}
            snap = {
                "state": {
                    "running": True,
                    "paused": self.paused_until > time.time(),
                    "paused_until": _iso_utc(self.paused_until)
                                    if self.paused_until else None,
                    "blocked_reason": reason or None,
                    "failsafe": self.failsafe,
                    "broker_connected": self.broker.is_connected(),
                    "symbol": self.cfg.SYMBOL,
                    "last_price": mid,
                    "band_low": self.strategy.cfg.band_low,
                    "band_high": self.strategy.cfg.band_high,
                    "balance": bal,
                    "equity": (bal + floating) if bal else None,
                    "floating_pnl": floating,
                    "config": self._config_dict(),
                    **cap,
                    **margin,
                },
                "positions": positions,
                "trades": trades,
                "trades_until": newest,
                "daily": daily,
                "account": {
                    "balance": bal or 0.0,
                    "equity": (bal or 0.0) + floating,
                    "floating_pnl": floating,
                    "open_positions": len(positions),
                },
            }
            with self._sync_lock:
                self._sync_snap = snap
        except Exception:  # noqa: BLE001 — zrkadlo nesmie zhodiť obchodovanie
            log.exception("Príprava Supabase snapshotu zlyhala")

    def _config_dict(self) -> dict:
        c = self.cfg
        return {
            "symbol": c.SYMBOL, "qty": c.QTY,
            "step_short": c.STEP_SHORT, "step_long": c.STEP_LONG,
            "tp_pct": self.strategy.cfg.tp_pct,
            "band_low": self.strategy.cfg.band_low,
            "band_high": self.strategy.cfg.band_high,
            "capacity": f"{c.CAP_BASE}+{c.CAP_RESERVE}",
            "tick_seconds": c.TICK_SECONDS,
            "failsafe_band": c.FAILSAFE_BAND, "s7_enabled": c.S7_ENABLED,
            "strategies": [s.status_line() for s in self.strategies],
        }

    def _sb_patch_pause_state(self) -> None:
        """Premietne pauzu do cache snapshotu hneď.

        Snapshot stavia obchodné vlákno raz za tick, takže bez tohto by
        push spustený tlačidlom odoslal ešte stav spred pauzy a dashboard
        by až do ďalšieho ticku tvrdil, že sa nič nestalo.

        _blocked_reason() sa smie volať aj odtiaľto — číta len pamäťové
        atribúty a kalendár, nie SQLite (to je viazané na obchodné vlákno).
        """
        with self._sync_lock:
            state = self._sync_snap.get("state")
            if state:
                state["paused"] = self.paused_until > time.time()
                state["paused_until"] = (_iso_utc(self.paused_until)
                                         if self.paused_until else None)
                # Bez tohto by po štarte ešte 10 s svietilo "vstupy
                # blokované: manuálna pauza", hoci pauza už neplatí.
                state["blocked_reason"] = self._blocked_reason() or None

    def _sb_pause(self, action: str, cmd: dict) -> None:
        """Beží v poller vlákne — mení jediný float, rovnako ako /pauza."""
        if action == "pause":
            mins = float((cmd.get("params") or {}).get("minutes", 60))
            self.paused_until = time.time() + mins * 60
            self.tg.send(f"⏸ Vstupy pozastavené na {mins:.0f} min "
                         f"(z dashboardu).")
            self.sync.finish(cmd["id"], True, f"pauza {mins:.0f} min")
        else:
            self.paused_until = 0.0
            self.tg.send("▶️ Vstupy povolené (z dashboardu).")
            self.sync.finish(cmd["id"], True, "vstupy povolené")
        self._sb_patch_pause_state()
        self.sync.trigger()

    def _drain_sb_commands(self) -> None:
        """Beží v obchodnom vlákne — sem chodí len 'close', lebo siaha na
        brokera aj DB."""
        while True:
            try:
                cmd = self._sb_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._sb_close(cmd)
            except Exception as exc:  # noqa: BLE001
                log.exception("Supabase príkaz %s zlyhal", cmd.get("id"))
                self.sync.finish(cmd["id"], False, str(exc))

    def _sb_close(self, cmd: dict) -> None:
        db_id = int(cmd["position_id"])
        row = self.db.conn.execute(
            "SELECT * FROM trades WHERE id=? AND status='open'",
            (db_id,)).fetchone()
        if row is None:
            self.sync.finish(cmd["id"], False,
                             f"#{db_id} nie je otvorená pozícia")
            return
        # Flag ide do DB PRED zatvorením: keby sa proces medzi príkazom
        # a reconcile reštartoval, obchod sa dopočíta ako ručný, nie ako TP.
        self.db.mark_manual_close(db_id)
        self.broker.close_position(row["entry_order_id"])
        self.tg.send(f"🖐 <b>{row['strategy']}</b> #{db_id} "
                     f"{row['side'].upper()} zatvorené ručne z dashboardu.")
        self.sync.finish(cmd["id"], True, f"#{db_id} zatvorené")
        # _poll_closes dopočíta reálnu cenu a P/L z dealu.
        self._poll_closes()
        self._refresh_sync_snapshot()
        self.sync.trigger()

    @staticmethod
    def _commands_help() -> str:
        return ("<b>Dostupné príkazy</b>\n"
                "/stav — balance, počet pozícií, poistka, stav vstupov\n"
                "/pozicie — zoznam otvorených pozícií s TP\n"
                "/pauza [30m|2h] — pozastaví vstupy (default 60 min)\n"
                "/start — zruší pauzu\n"
                "/help — tento zoznam")


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
