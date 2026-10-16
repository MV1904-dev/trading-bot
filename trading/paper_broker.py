"""Virtuálny cTrader účet — verná simulácia brokera pre súťažnú stratégiu.

Nekomunikuje s brokerom. Ceny dostáva zo živého bota (data/feed.json) a
účtuje presne tak, ako to robí live účet — každé pravidlo je overené proti
live obchodom (Supabase, 6. 8. – 16. 10. 2026):

* vstup trhovým príkazom: long za ASK, short za BID aktuálnej kotácie
* TP je limitka u brokera: long sa zavrie, keď BID dosiahne TP, short, keď
  ASK klesne na TP — kontroluje sa na extrémoch medzi kotáciami (bot ich
  zbiera zo spot streamu), nie len na 10 s vzorkách, takže TP medzi
  vzorkami sa nestratí. Plní sa presne na TP (live plní v priemere o
  0,2 pipu lepšie — simulácia je o chlp konzervatívnejšia)
* provízia 0,526 € na cyklus (live priemer), polovica pri otvorení,
  polovica pri zatvorení
* swap: rollover 17:00 New York po–pi, v deň `swap_rollover_3days`
  trojnásobok; sadzba v pipoch zo živého účtu (bot_state.swap_long/short),
  takže sa mení presne vtedy, keď ju zmení broker
* P/L v USD sa prepočíta na EUR aktuálnym kurzom (účet je v EUR)
* marža: 1:30, pri protismerných pozíciách len z väčšej strany (hedging,
  overené 15. 10. 2026: 25 L / 3 S → 8 333,25)
* equity = zostatok + float + naakumulovaný swap otvorených pozícií
* stop-out 50 %: zatvára sa najstratovejšia pozícia, kým úroveň marže
  nie je znova nad hranicou

Stĺpce v DB sa volajú *_usd (zdedená schéma trading/botdb.py), hodnoty sú
v EUR — rovnako ako pri live bote.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from trading.botdb import BotDB

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")

COMMISSION_RT = 0.526       # € na cyklus pri 10 000 jednotkách
PIP = 0.0001


@dataclass
class Quote:
    ts: float
    bid: float
    ask: float
    # extrémy od predošlej kotácie (zo spot streamu); bez nich = bid/ask
    bid_hi: Optional[float] = None
    ask_lo: Optional[float] = None
    bid_lo: Optional[float] = None
    ask_hi: Optional[float] = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


class PaperAccount:
    def __init__(self, db: BotDB, capital: float, leverage: float = 30.0,
                 stop_out: float = 50.0):
        self.db = db
        self.leverage = leverage
        self.stop_out_level = stop_out
        if not self.db.meta_get("capital"):
            self.db.meta_set("capital", capital)
            self.db.meta_set("started_at", "")
        self.capital = float(self.db.meta_get("capital"))
        self.stopouts = int(self.db.meta_get("stopouts", "0") or 0)

    # --- stav -------------------------------------------------------------
    def open_rows(self) -> list:
        return self.db.open_trades()

    def balance(self) -> float:
        """Vklad + uzavreté obchody (hrubý zisk + swap − provízia) − provízie
        už zaplatené pri otvorení dnes otvorených pozícií."""
        r = self.db.conn.execute(
            "SELECT COALESCE(SUM(pnl_usd + funding_usd - commission_usd), 0) s "
            "FROM trades WHERE status='closed'").fetchone()
        o = self.db.conn.execute(
            "SELECT COALESCE(SUM(commission_usd), 0) s FROM trades "
            "WHERE status='open'").fetchone()
        return self.capital + r["s"] - o["s"]

    @staticmethod
    def _gross(row, bid: float, ask: float) -> float:
        """P/L pozície v EUR, ak by sa zavrela teraz (long za BID, short za ASK)."""
        mid = (bid + ask) / 2
        if row["side"] == "long":
            return (bid - row["entry_price"]) * row["qty"] / mid
        return (row["entry_price"] - ask) * row["qty"] / mid

    def floating(self, q: Quote) -> float:
        return sum(self._gross(r, q.bid, q.ask) for r in self.open_rows())

    def open_swap(self) -> float:
        r = self.db.conn.execute(
            "SELECT COALESCE(SUM(funding_usd), 0) s FROM trades "
            "WHERE status='open'").fetchone()
        return r["s"]

    def equity(self, q: Quote) -> float:
        return self.balance() + self.floating(q) + self.open_swap()

    def used_margin(self) -> float:
        """Hedging: marža len z väčšej strany (účet v EUR = základná mena)."""
        rows = self.open_rows()
        ql = sum(r["qty"] for r in rows if r["side"] == "long")
        qs = sum(r["qty"] for r in rows if r["side"] == "short")
        return max(ql, qs) / self.leverage

    def margin_level(self, q: Quote) -> Optional[float]:
        used = self.used_margin()
        return self.equity(q) / used * 100 if used > 0 else None

    # --- obchody ------------------------------------------------------------
    def open(self, strategy: str, side: str, qty: float, q: Quote,
             tp: float, context: Optional[dict] = None) -> tuple[int, float]:
        entry = q.ask if side == "long" else q.bid
        ctx = dict(context or {})
        ctx["spread_at_entry"] = round(q.ask - q.bid, 6)
        half = COMMISSION_RT * qty / 10_000 / 2
        cur = self.db.conn.execute(
            "INSERT INTO trades(strategy,side,qty,ts_open,entry_price,tp_price,"
            "entry_order_id,tp_order_id,commission_usd,context) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (strategy, side, qty, q.ts, entry, tp, None, None, half,
             json.dumps(ctx)))
        tid = cur.lastrowid
        self.db.conn.execute("UPDATE trades SET entry_order_id=? WHERE id=?",
                             (tid, tid))
        self.db.conn.commit()
        return tid, entry

    def close(self, row, price: float, mid: float, ts: float,
              reason: str = "tp") -> float:
        diff = (price - row["entry_price"]) if row["side"] == "long" \
            else (row["entry_price"] - price)
        pnl = diff * row["qty"] / mid
        half = COMMISSION_RT * row["qty"] / 10_000 / 2
        ctx = json.loads(row["context"] or "{}")
        ctx["close_reason"] = reason
        self.db.conn.execute(
            "UPDATE trades SET status='closed', ts_close=?, close_price=?, "
            "pnl_usd=?, commission_usd=commission_usd+?, context=?, "
            "manual_close=? WHERE id=?",
            (ts, price, pnl, half, json.dumps(ctx),
             1 if reason != "tp" else 0, row["id"]))
        self.db.conn.commit()
        return pnl

    def check_tp(self, q: Quote) -> list[tuple[int, str, float]]:
        """TP limitky: long na BID ≥ TP, short na ASK ≤ TP (vrátane extrémov
        medzi kotáciami). Vracia [(id, strana, cena)]."""
        bid_hi = max(q.bid, q.bid_hi or q.bid)
        ask_lo = min(q.ask, q.ask_lo or q.ask)
        done = []
        for r in self.open_rows():
            tp = r["tp_price"]
            if r["side"] == "long" and bid_hi >= tp:
                self.close(r, tp, q.mid, q.ts)
                done.append((r["id"], "long", tp))
            elif r["side"] == "short" and ask_lo <= tp:
                self.close(r, tp, q.mid, q.ts)
                done.append((r["id"], "short", tp))
        return done

    def enforce_level(self, q: Quote, level: float,
                      reason: str) -> list[tuple[int, str, float]]:
        """Kým je úroveň marže pod `level`, zavrie najstratovejšiu pozíciu.
        Na horšom konci kotácie — broker stop-out reaguje na tick, nie na
        10 s vzorku."""
        rows = self.open_rows()
        ql = sum(r["qty"] for r in rows if r["side"] == "long")
        qs = sum(r["qty"] for r in rows if r["side"] == "short")
        # Väčšia strana stráca na jednom konci kotácie: long-heavy účet na
        # spodnom (nízky BID aj ASK), short-heavy na hornom.
        if ql >= qs:
            qx = Quote(q.ts, min(q.bid, q.bid_lo or q.bid), min(q.ask, q.ask_lo or q.ask))
        else:
            qx = Quote(q.ts, max(q.bid, q.bid_hi or q.bid), max(q.ask, q.ask_hi or q.ask))
        done = []
        while True:
            ml = self.margin_level(qx)
            if ml is None or ml >= level:
                return done
            rows = self.open_rows()
            worst = min(rows, key=lambda r: self._gross(r, qx.bid, qx.ask))
            price = qx.bid if worst["side"] == "long" else qx.ask
            self.close(worst, price, qx.mid, q.ts, reason=reason)
            done.append((worst["id"], worst["side"], price))
            if reason == "stop-out":
                self.stopouts += 1
                self.db.meta_set("stopouts", self.stopouts)
            log.warning("%s: zavretá #%d %s @ %.5f (marža %.0f %%)",
                        reason, worst["id"], worst["side"], price, ml)

    # --- swap ---------------------------------------------------------------
    @staticmethod
    def rollovers_between(t0: float, t1: float) -> list[datetime]:
        """Rollovery 17:00 New York (po–pi) v intervale (t0, t1]."""
        out = []
        d = datetime.fromtimestamp(t0, NY).replace(hour=17, minute=0,
                                                   second=0, microsecond=0)
        if d.timestamp() <= t0:
            d += timedelta(days=1)
        while d.timestamp() <= t1:
            if d.weekday() < 5:
                out.append(d)
            d = (d + timedelta(days=1)).replace(hour=17)
        return out

    def rollover(self, q: Quote, swap_long_pips: float, swap_short_pips: float,
                 triple_dow: int = 3) -> int:
        """Pripíše swap za rollovery od posledného spracovaného. triple_dow
        je ProtoOADayOfWeek (1 = pondelok … 3 = streda)."""
        last = float(self.db.meta_get("last_rollover_check", "0") or 0)
        if last <= 0:
            self.db.meta_set("last_rollover_check", q.ts)
            return 0
        n = 0
        for d in self.rollovers_between(last, q.ts):
            mult = 3 if d.weekday() + 1 == triple_dow else 1
            day = d.strftime("%Y-%m-%d")
            for r in self.open_rows():
                if r["ts_open"] >= d.timestamp():
                    continue
                pips = swap_long_pips if r["side"] == "long" else swap_short_pips
                amount = pips * PIP * r["qty"] / q.mid * mult
                self.db.add_funding(r["id"], day, round(amount, 4))
                n += 1
        self.db.meta_set("last_rollover_check", q.ts)
        return n
