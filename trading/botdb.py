"""SQLite vrstva bota (data/bot.db).

Tabuľky
-------
signals        každý signál stratégie — aj nevykonaný (action + reason)
trades         obchody s kontextom; funding_usd sa priebežne akumuluje
funding        denné funding záznamy per pozícia
account_daily  denný snapshot účtu
events         prevádzkové udalosti bota (štart, pauza, chyby, alarmy)
meta           kľúč/hodnota (napr. telegram offset)
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    strategy TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL,
    atr REAL,
    spread REAL,
    action TEXT NOT NULL,          -- executed | blocked | error
    reason TEXT,
    context TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    side TEXT NOT NULL,            -- long | short
    qty REAL NOT NULL,
    ts_open REAL NOT NULL,
    entry_price REAL NOT NULL,
    tp_price REAL NOT NULL,
    entry_order_id INTEGER,
    tp_order_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open',   -- open | closed
    ts_close REAL,
    close_price REAL,
    pnl_usd REAL,
    funding_usd REAL NOT NULL DEFAULT 0,
    commission_usd REAL NOT NULL DEFAULT 0,
    context TEXT
);
CREATE TABLE IF NOT EXISTS funding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    UNIQUE(trade_id, day)
);
CREATE TABLE IF NOT EXISTS account_daily (
    day TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    net_liquidation REAL,
    cash REAL,
    floating_pnl REAL,
    open_positions INTEGER,
    cycles_prev_day INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    level TEXT NOT NULL,           -- info | warn | alarm
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class BotDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- signals ----------------------------------------------------------
    def log_signal(self, strategy: str, side: str, price: float, atr: float,
                   spread: float, action: str, reason: str = "",
                   context: Optional[dict] = None) -> None:
        self.conn.execute(
            "INSERT INTO signals(ts,strategy,side,price,atr,spread,action,"
            "reason,context) VALUES (?,?,?,?,?,?,?,?,?)",
            (time.time(), strategy, side, price, atr, spread, action, reason,
             json.dumps(context or {})))
        self.conn.commit()

    # --- trades -----------------------------------------------------------
    def open_trade(self, strategy: str, side: str, qty: float,
                   entry_price: float, tp_price: float,
                   entry_order_id: int, tp_order_id: int,
                   commission_usd: float = 0.0,
                   context: Optional[dict] = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO trades(strategy,side,qty,ts_open,entry_price,"
            "tp_price,entry_order_id,tp_order_id,commission_usd,context) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (strategy, side, qty, time.time(), entry_price, tp_price,
             entry_order_id, tp_order_id, commission_usd,
             json.dumps(context or {})))
        self.conn.commit()
        return cur.lastrowid

    def set_tp_order(self, trade_id: int, tp_order_id: int,
                     tp_price: float) -> None:
        self.conn.execute(
            "UPDATE trades SET tp_order_id=?, tp_price=? WHERE id=?",
            (tp_order_id, tp_price, trade_id))
        self.conn.commit()

    def close_trade(self, trade_id: int, close_price: float, pnl_usd: float,
                    commission_usd: float = 0.0) -> None:
        self.conn.execute(
            "UPDATE trades SET status='closed', ts_close=?, close_price=?, "
            "pnl_usd=?, commission_usd=commission_usd+? WHERE id=?",
            (time.time(), close_price, pnl_usd, commission_usd, trade_id))
        self.conn.commit()

    def open_trades(self, strategy: Optional[str] = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM trades WHERE status='open'"
        args: tuple = ()
        if strategy:
            q += " AND strategy=?"
            args = (strategy,)
        return list(self.conn.execute(q + " ORDER BY id", args))

    def cycles_on_day(self, day: str) -> int:
        """Počet zavretých obchodov s ts_close v daný UTC deň (YYYY-MM-DD)."""
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM trades WHERE status='closed' AND "
            "date(ts_close,'unixepoch')=?", (day,)).fetchone()
        return row["c"]

    # --- funding ----------------------------------------------------------
    def add_funding(self, trade_id: int, day: str, amount_usd: float) -> None:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO funding(trade_id,day,amount_usd) "
            "VALUES (?,?,?)", (trade_id, day, amount_usd))
        if cur.rowcount:
            self.conn.execute(
                "UPDATE trades SET funding_usd=funding_usd+? WHERE id=?",
                (amount_usd, trade_id))
        self.conn.commit()

    # --- account / events / meta -----------------------------------------
    def snapshot_account(self, day: str, net_liq: float, cash: float,
                         floating: float, open_pos: int,
                         cycles_prev: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO account_daily VALUES (?,?,?,?,?,?,?)",
            (day, time.time(), net_liq, cash, floating, open_pos, cycles_prev))
        self.conn.commit()

    def log_event(self, level: str, message: str) -> None:
        self.conn.execute("INSERT INTO events(ts,level,message) VALUES (?,?,?)",
                          (time.time(), level, message))
        self.conn.commit()

    def meta_get(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?",
                                (key,)).fetchone()
        return row["value"] if row else default

    def meta_set(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                          (key, str(value)))
        self.conn.commit()
