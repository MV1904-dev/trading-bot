#!/usr/bin/env python3
"""Virtuálne účty — súťažné stratégie na simulovanom cTrader účte.

Ktoré stratégie bežia, určuje trading/paper_registry.py (každá vlastná DB
a env v Supabase). Prvá bola Strážca (16. 10. 2026).

Zadanie 16. 10. 2026: vlastná stratégia na virtuálnom účte, verná
simulácia cTradera, súťaž so živým gridom od pondelka 19. 10.

Prečo nie skutočný demo účet: Spotware drží na jednu aplikáciu jedno
app-auth spojenie (docs/prevadzka.md) — druhý proces s tou istou
aplikáciou by odpojil živého bota. Tento proces sa preto k brokerovi
NEPRIPÁJA vôbec. Ceny číta zo súboru data/feed.json, ktorý živý bot
prepisuje každých 10 s (bid, ask, extrémy zo spot streamu, swapové sadzby).
Keď feed zastará, virtuálny bot neotvára nič a čaká.

Účtovanie: trading/paper_broker.py (spread, provízia, swap, hedging
marže, stop-out — kalibrované na live obchody).
Stratégia: trading/strategy_guard.py.
Zrkadlo: Supabase s env='paper' (rovnaké tabuľky ako live, dashboard
prepína LIVE / VS / DEMO). Príkazy z dashboardu NEČÍTA — tabuľka commands
nemá env a príkazy patria živému botovi.

    python3 paper_bot.py                         # prevádzka (systemd ctrader-paper)
    python3 paper_bot.py --replay 2026-08-06     # test na histórii z labu, bez Supabase
    python3 paper_bot.py --replay 2026-08-06 --strategy paper-dobeh
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from trading.botdb import BotDB  # noqa: E402
from trading.macro import MacroCalendar  # noqa: E402
from trading.paper_broker import PaperAccount, Quote  # noqa: E402
from trading.strategy_base import Bar  # noqa: E402
from trading.paper_registry import VIRTUALS, Virtual  # noqa: E402
from trading.strategy_base import StrategyBase  # noqa: E402
from trading.sync_supabase import SupabaseSync  # noqa: E402

log = logging.getLogger("paper_bot")

FEED = ROOT / "data" / "feed.json"
PLAN_CANDLES = ROOT / "data" / "plan_candles.json"
PAPER_DIR = ROOT / "data" / "paper"
FEED_STALE_S = 60.0
POLL_S = 2.0
BAR_S = 300
ATR_N = 14
PUSH_EVERY_S = 60.0
TZ = ZoneInfo("Europe/Bratislava")


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


class PaperBot:
    def __init__(self, db_path: Path, capital: float, sync: SupabaseSync | None,
                 macro: MacroCalendar | None, spec: Virtual = VIRTUALS[0]):
        self.spec = spec
        self.db = BotDB(db_path)
        self.acct = PaperAccount(self.db, capital)
        self.strat: StrategyBase = spec.make()
        self.sync = sync
        self.macro = macro
        self.bar: Bar | None = None
        self.atr: float | None = None
        self.atr_n = 0
        self.prev_close: float | None = None
        self.last_q: Quote | None = None
        self.feed: dict = {}
        self.blocked: str | None = None
        self._snap: dict = {}
        self._lock = threading.Lock()
        self._restore()

    # --- štart ----------------------------------------------------------------
    def _restore(self) -> None:
        rows = self.db.open_trades()
        self.strat.restore(rows)
        ref = self.db.meta_get("ref", "")
        if ref:
            rl, rs, ll, ls = (float(x) for x in ref.split("|"))
            self.strat.ref_long = rl or None
            self.strat.ref_short = rs or None
            self.strat.last_long, self.strat.last_short = ll, ls
        atr = self.db.meta_get("atr", "")
        if atr:
            self.atr, self.atr_n = float(atr), ATR_N
        if rows:
            log.info("Obnova: %d otvorených pozícií, kotvy %s", len(rows), ref)

    def warmup_from_plan_candles(self, now: float) -> None:
        """H1 trend z sviečok, ktoré živý bot sype pre Daily Plan (2000 H1).
        Len uzavreté hodiny — tvoriaca sa hodina by sa započítala dvakrát."""
        try:
            data = json.loads(PLAN_CANDLES.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("[%s] Zahriatie z H1 sviečok zlyhalo (%s).", self.spec.name, exc)
            return
        cur_hour = int(now // 3600)
        bars = [Bar(c["time"], c["o"], c["h"], c["l"], c["c"])
                for c in data.get("h1", []) if int(c["time"] // 3600) < cur_hour]
        self.strat.warmup(bars)
        log.info("[%s] zahriate z %d H1 barov: %s", self.spec.name, len(bars),
                 self.strat.status_line())

    # --- jedna kotácia ----------------------------------------------------------
    def step(self, q: Quote, swap_long: float, swap_short: float,
             triple_dow: int, connected: bool = True) -> None:
        a = self.acct
        if hasattr(self.strat, "on_feed"):
            self.strat.on_feed(self.feed, q.mid)
        a.rollover(q, swap_long, swap_short, triple_dow)
        for tid, side, px in a.check_tp(q):
            self.strat.on_trade_closed(tid, side, px)
            log.info("[%s] TP #%d %s @ %.5f", self.spec.name, tid, side, px)
        for tid, side, px in a.enforce_level(q, a.stop_out_level, "stop-out"):
            self.strat.on_trade_closed(tid, side, px)
        guard = getattr(self.strat.cfg, "guard_ml", None)
        if guard:
            for tid, side, px in a.enforce_level(q, guard, "strážca marže"):
                self.strat.on_trade_closed(tid, side, px)
        self.last_q = q
        self.blocked = self._blocked_reason(q, connected)
        self._aggregate(q)

    def _blocked_reason(self, q: Quote, connected: bool) -> str | None:
        if not connected:
            return "živý bot nemá spojenie s brokerom"
        if self.macro is not None:
            ev = self.macro.active_blackout(q.ts)
            if ev:
                t = datetime.fromtimestamp(ev["ts"], TZ).strftime("%H:%M")
                return f"makro blackout: {ev['currency']} {ev['title']} o {t}"
        return None

    def _aggregate(self, q: Quote) -> None:
        """M5 bary z mid cien — rovnako ako live bot (_aggregate_bar)."""
        mid = q.mid
        bucket = int(q.ts // BAR_S)
        if self.bar is None:
            self.bar = Bar(bucket * BAR_S, mid, mid, mid, mid)
            return
        if bucket == int(self.bar.ts // BAR_S):
            self.bar.high = max(self.bar.high, mid)
            self.bar.low = min(self.bar.low, mid)
            self.bar.close = mid
            return
        closed, self.bar = self.bar, Bar(bucket * BAR_S, mid, mid, mid, mid)
        pc = self.prev_close if self.prev_close is not None else closed.open
        tr = max(closed.high - closed.low, abs(closed.high - pc), abs(closed.low - pc))
        self.atr = tr if self.atr is None else self.atr * (ATR_N - 1) / ATR_N + tr / ATR_N
        self.atr_n += 1
        self.prev_close = closed.close
        atr = self.atr if self.atr_n >= ATR_N else None
        for sig in self.strat.on_bar(closed, atr):
            if self.blocked:
                self.db.log_signal(self.strat.id, sig.side, q.mid, atr or 0.0,
                                   q.ask - q.bid, "blocked", self.blocked, sig.context)
                continue
            tid, entry = self.acct.open(self.strat.id, sig.side, sig.qty, q,
                                        sig.tp_price, sig.context | {"reason": sig.reason})
            self.strat.on_trade_opened(tid, sig.side, entry)
            self.db.log_signal(self.strat.id, sig.side, entry, atr or 0.0,
                               q.ask - q.bid, "executed", sig.reason, sig.context)
            log.info("[%s] vstup #%d %s @ %.5f TP %.5f — %s", self.spec.name, tid,
                     sig.side, entry, sig.tp_price, sig.reason)
        s = self.strat
        self.db.meta_set("ref", f"{s.ref_long or 0}|{s.ref_short or 0}|"
                                f"{s.last_long}|{s.last_short}")
        if self.atr is not None:
            self.db.meta_set("atr", self.atr)

    # --- prevádzka ---------------------------------------------------------------
    def start(self) -> None:
        self.warmup_from_plan_candles(time.time())
        if not self.db.meta_get("started_at"):
            self.db.meta_set("started_at", time.time())

    def push(self) -> None:
        """Volá push vlákno — len odošle hotový snapshot, DB nečíta."""
        with self._lock:
            snap = dict(self._snap)
        if not snap or self.sync is None:
            return
        self.sync.push(snap)
        with self._lock:
            self._confirmed = self.sync.confirmed_trades_until

    def _prepare_snapshot(self) -> None:
        if self.sync is None or not self.sync.enabled or self.last_q is None:
            return
        q, a = self.last_q, self.acct
        fresh = time.time() - q.ts <= FEED_STALE_S
        positions, floating = [], 0.0
        for r in a.open_rows():
            ctx = json.loads(r["context"] or "{}")
            pnl = a._gross(r, q.bid, q.ask)
            floating += pnl
            positions.append({
                "id": r["id"], "broker_position_id": r["id"],
                "strategy": r["strategy"], "symbol": "EURUSD", "side": r["side"],
                "qty": r["qty"], "entry_price": r["entry_price"],
                "tp_price": r["tp_price"], "opened_at": _iso(r["ts_open"]),
                "funding_usd": r["funding_usd"], "commission_usd": r["commission_usd"],
                "pnl_float": pnl, "spread_at_entry": ctx.get("spread_at_entry"),
                "spread_cost_usd": None, "context": ctx,
                "updated_at": _iso(time.time()),
            })
        since = float(self.db.meta_get("sb_trades_until", "0") or 0)
        confirmed = getattr(self, "_confirmed", 0.0)
        if confirmed > since:
            self.db.meta_set("sb_trades_until", confirmed)
            since = confirmed
        trades, newest = [], since
        for r in self.db.conn.execute(
                "SELECT * FROM trades WHERE status='closed' AND ts_close >= ? "
                "ORDER BY ts_close", (max(since - 3600, 0),)):
            ctx = json.loads(r["context"] or "{}")
            gross, comm, fund = r["pnl_usd"] or 0.0, r["commission_usd"] or 0.0, r["funding_usd"] or 0.0
            trades.append({
                "id": r["id"], "broker_position_id": r["id"], "strategy": r["strategy"],
                "symbol": "EURUSD", "side": r["side"], "qty": r["qty"],
                "entry_price": r["entry_price"], "tp_price": r["tp_price"],
                "close_price": r["close_price"], "opened_at": _iso(r["ts_open"]),
                "closed_at": _iso(r["ts_close"]), "gross_pnl_usd": gross,
                "pnl_usd": gross - comm + fund, "commission_usd": comm,
                "funding_usd": fund, "spread_at_entry": ctx.get("spread_at_entry"),
                "spread_cost_usd": None, "manual_close": bool(r["manual_close"]),
                "context": ctx,
            })
            newest = max(newest, r["ts_close"] or 0)
        bal = a.balance()
        n = len(positions)
        used_sum = n * 10_000 / a.leverage      # dashboard čaká súčet, hedging si prepočíta sám
        eq_true = bal + floating + a.open_swap()
        used = a.used_margin()
        cfg = self._config()
        c = self.strat.cfg
        state = {
            "running": True, "data_at": _iso(time.time()),
            "paused": False, "paused_until": None,
            "blocked_reason": self.blocked or (None if fresh else "feed zo živého bota zastaral"),
            "failsafe": False, "broker_connected": fresh and bool(self.feed.get("connected", True)),
            "symbol": "EURUSD", "last_price": q.mid,
            "band_low": c.band_low, "band_high": c.band_high,
            "balance": bal, "equity": bal + floating, "floating_pnl": floating,
            "deposits_net": a.capital, "deposits_count": 1,
            "used_margin": used_sum,
            "free_margin": eq_true - used,
            "margin_level": eq_true / used * 100 if used else None,
            "swap_long": self.feed.get("swap_long"), "swap_short": self.feed.get("swap_short"),
            "swap_rollover_3days": self.feed.get("swap_rollover_3days"),
            "config": cfg,
        }
        snap = {
            "state": state, "positions": positions, "trades": trades,
            "trades_until": newest, "daily": self._daily_rows(),
            "account": {"balance": bal, "equity": bal + floating,
                        "floating_pnl": floating, "open_positions": n},
        }
        with self._lock:
            self._snap = snap

    def _config(self) -> dict:
        """bot_state.config — spoločné polia + to, čo daná stratégia má."""
        s, c = self.strat, self.strat.cfg
        cfg = {
            "symbol": "EURUSD", "qty": c.qty, "tp_pct": c.tp_pct,
            "band_low": c.band_low, "band_high": c.band_high,
            "ref_long": s.ref_long, "ref_short": s.ref_short,
            "atr": self.atr, "atr_mult": c.atr_mult,
            "strategy_name": self.spec.name, "strategy_note": self.spec.note,
            "virtual": True,
            "started_at": _iso(float(self.db.meta_get("started_at") or 0) or None),
            "stopouts": self.acct.stopouts,
            "strategies": [s.status_line()],
        }
        if hasattr(s, "trend"):                 # Strážca
            tr = s.trend()
            cfg.update({
                "step_long": c.step if tr >= 0 else c.step_ct,
                "step_short": c.step if tr <= 0 else c.step_ct,
                "long_enabled": True, "short_enabled": True,
                "capacity": f"{c.cap} v trende / {c.cap_ct} proti",
                "trend": tr, "ema_fast": s.fast.v, "ema_slow": s.slow.v,
            })
        else:                                   # grid (Grid25 a odvodené)
            cfg.update({
                "step_long": c.step_long, "step_short": c.step_short,
                "long_enabled": c.long_enabled, "short_enabled": c.short_enabled,
                "capacity": f"{c.base_levels}+{c.reserve_levels}",
                "base_levels": c.base_levels, "reserve_levels": c.reserve_levels,
            })
        return cfg

    def _daily_rows(self) -> list[dict]:
        acc: dict[tuple, dict] = {}
        for r in self.db.conn.execute(
                "SELECT ts_close, strategy, pnl_usd, commission_usd, funding_usd "
                "FROM trades WHERE status='closed' AND ts_close IS NOT NULL"):
            day = datetime.fromtimestamp(r["ts_close"], TZ).strftime("%Y-%m-%d")
            comm, fund = r["commission_usd"] or 0.0, r["funding_usd"] or 0.0
            net = (r["pnl_usd"] or 0.0) - comm + fund
            d = acc.setdefault((day, r["strategy"]), {
                "day": day, "strategy": r["strategy"], "cycles": 0, "pnl_usd": 0.0,
                "commission_usd": 0.0, "funding_usd": 0.0, "wins": 0, "losses": 0})
            d["cycles"] += 1
            d["pnl_usd"] += net
            d["commission_usd"] += comm
            d["funding_usd"] += fund
            d["wins" if net > 0 else "losses"] += 1
        rows = sorted(acc.values(), key=lambda d: d["day"], reverse=True)
        for d in rows:
            for k in ("pnl_usd", "commission_usd", "funding_usd"):
                d[k] = round(d[k], 2)
        return rows[:120]


# --- viac účtov naraz ---------------------------------------------------------------

def read_feed() -> tuple[Quote, dict] | None:
    try:
        f = json.loads(FEED.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    q = Quote(f["ts"], f["bid"], f["ask"], f.get("bid_hi"), f.get("ask_lo"),
              f.get("bid_lo"), f.get("ask_hi"))
    return q, f


def run_all(bots: list[PaperBot], macro: MacroCalendar | None) -> None:
    """Jeden feed, všetky virtuálne účty. Chyba jedného účtu ostatné nezastaví."""
    for b in bots:
        b.start()
    threading.Thread(target=_push_all, args=(bots,), name="sb-push", daemon=True).start()
    last_ts = 0.0
    stale_logged = False
    while True:
        got = read_feed()
        if got is not None:
            q, f = got
            if q.ts > last_ts:
                last_ts = q.ts
                stale_logged = False
                for b in bots:
                    b.feed = f
                    try:
                        b.step(q, f.get("swap_long") or 0.0, f.get("swap_short") or 0.0,
                               int(f.get("swap_rollover_3days") or 3),
                               bool(f.get("connected", True)))
                    except Exception:  # noqa: BLE001
                        log.exception("[%s] spracovanie kotácie zlyhalo", b.spec.name)
            elif time.time() - q.ts > FEED_STALE_S and not stale_logged:
                stale_logged = True
                log.warning("Feed zo živého bota je starý %.0f s — čakám.",
                            time.time() - q.ts)
        if macro is not None:
            macro.refresh()
        for b in bots:
            try:
                b._prepare_snapshot()
            except Exception:  # noqa: BLE001
                log.exception("[%s] príprava snapshotu zlyhala", b.spec.name)
        time.sleep(POLL_S)


def _push_all(bots: list[PaperBot]) -> None:
    while True:
        for b in bots:
            try:
                b.push()
            except Exception:  # noqa: BLE001
                log.exception("[%s] push do Supabase zlyhal", b.spec.name)
        time.sleep(PUSH_EVERY_S)


# --- replay ----------------------------------------------------------------------

def replay(start: str, end: str | None, spec: Virtual = VIRTUALS[0]) -> int:
    """Prehrá M5 sviečky z labu ako kotácie (open → extrémy → close) a vypíše
    výsledok — overenie, že virtuálny účet počíta ako lab."""
    import tempfile

    from trading.rates import funding_rates_pa
    src = ROOT / "data" / "lab_ladder" / "EURUSD_M5_20241015_20261015.csv"
    t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp()
    t1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp() if end else 9e9
    with src.open() as fh:
        bars = [tuple(float(x) for x in r) for r in csv.reader(fh)]
    bars = [b for b in bars if t0 <= b[0] < t1]
    spread = 0.00001
    with tempfile.TemporaryDirectory() as tmp:
        bot = PaperBot(Path(tmp) / "replay.db", 15_200.0, None, None, spec)
        peak, max_dd, min_ml = 15_200.0, 0.0, float("inf")
        for t, o, h, l, c in bars:
            day = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
            lpa, spa = funding_rates_pa(day)
            # % p. a. → pipy na noc pri 10 000 jednotkách (kurz ~ c)
            sl = -lpa / 100 / 365 * c / 0.0001
            ss = spa / 100 / 365 * c / 0.0001
            up = c >= o
            path = [(t, o), (t + 100, l if up else h), (t + 200, h if up else l), (t + 290, c)]
            prev = o
            for ts, px in path:
                lo_, hi_ = min(prev, px), max(prev, px)
                q = Quote(ts, px, px + spread, bid_hi=hi_, ask_lo=lo_ + spread,
                          bid_lo=lo_, ask_hi=hi_ + spread)
                bot.step(q, sl, ss, 3)
                prev = px
            eq = bot.acct.equity(bot.last_q)
            peak = max(peak, eq)
            max_dd = max(max_dd, peak - eq)
            ml = bot.acct.margin_level(bot.last_q)
            if ml is not None:
                min_ml = min(min_ml, ml)
        a = bot.acct
        closed = bot.db.conn.execute(
            "SELECT COUNT(*) n, SUM(pnl_usd) g, SUM(funding_usd) f, SUM(commission_usd) c "
            "FROM trades WHERE status='closed'").fetchone()
        rows = a.open_rows()
        print(f"replay {spec.name}: {start} → {end or 'koniec'}, {len(bars)} M5 barov")
        print(f"  cyklov {closed['n']}, hrubý {closed['g'] or 0:.0f}, swap uzavretých "
              f"{closed['f'] or 0:.0f}, provízie {closed['c'] or 0:.0f}")
        print(f"  zostatok {a.balance():.0f}, equity {eq:.0f} (výsledok {eq - 15_200:+.0f}), "
              f"max prepad {max_dd:.0f}, min marža {min_ml:.0f} %, stop-out {a.stopouts}×")
        print(f"  otvorené L/S {sum(r['side'] == 'long' for r in rows)}/"
              f"{sum(r['side'] == 'short' for r in rows)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Virtuálne účty — súťažné stratégie")
    ap.add_argument("--replay", metavar="OD", help="prehrať históriu od dátumu (YYYY-MM-DD)")
    ap.add_argument("--until", metavar="DO", help="koniec prehrávania")
    ap.add_argument("--strategy", metavar="ENV", default=VIRTUALS[0].env,
                    help="pri --replay: env účtu z trading/paper_registry.py")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING if args.replay else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.replay:
        spec = next((v for v in VIRTUALS if v.env == args.strategy), None)
        if spec is None:
            ap.error(f"neznámy účet {args.strategy}; sú: {', '.join(v.env for v in VIRTUALS)}")
        return replay(args.replay, args.until, spec)
    from dotenv import load_dotenv   # na serveri je; replay ju nepotrebuje
    load_dotenv()
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    logging.getLogger().addHandler(logging.FileHandler(PAPER_DIR / "paper_bot.log"))
    capital = float(os.getenv("PAPER_CAPITAL", "15200"))
    macro = MacroCalendar(PAPER_DIR / "ff_calendar.json")
    bots = []
    for v in VIRTUALS:
        b = PaperBot(PAPER_DIR / v.db, capital, SupabaseSync(env=v.env), macro, v)
        bots.append(b)
        log.info("Virtuálny účet %s (%s) — kapitál %.0f €, %s", v.name, v.env,
                 b.acct.capital, b.strat.status_line())
    try:
        run_all(bots, macro)
    except KeyboardInterrupt:
        log.info("Zastavené.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
