#!/usr/bin/env python3
"""Denník a vyhodnocovacia slučka (§6, §7).

Bez tejto vrstvy sa systém podľa zadania nespúšťa — hodnota celého
procesu je v tom, že po ~100 obchodoch sa dá povedať, či má kladnú
očakávanú hodnotu.

Virtuálna equity: Daily Plan beží na zdieľanom účte s gridom, takže si
vedie vlastnú equity (počiatočný vklad + vlastné realizované P/L +
floating vlastných pozícií). Limity z §4 sa počítajú z nej, nie zo
zostatku účtu, ktorý hýbe grid.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent / "journal.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date TEXT NOT NULL,
    scenario TEXT NOT NULL,           -- P | A1
    side TEXT NOT NULL,
    broker_position_id INTEGER,
    label TEXT,
    planned_entry REAL, planned_sl REAL, planned_tp1 REAL, planned_tp2 REAL,
    planned_volume REAL,
    ts_open REAL, actual_entry REAL, actual_volume REAL,
    ts_close REAL, actual_exit REAL,
    exit_reason TEXT,                 -- tp1 | tp2 | sl | time_stop | friday
                                      -- | news_guard | manual | kill_switch
    slippage_entry_pips REAL, slippage_exit_pips REAL,
    mfe_pips REAL, mae_pips REAL,
    gross_eur REAL, commission_eur REAL, swap_eur REAL, net_eur REAL,
    r_multiple REAL,                  -- net / plánované riziko
    manual INTEGER NOT NULL DEFAULT 0,
    UNIQUE(plan_date, scenario)
);
CREATE TABLE IF NOT EXISTS deviations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, plan_date TEXT, kind TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    day TEXT PRIMARY KEY,
    virtual_equity REAL, realized REAL, floating REAL,
    hwm REAL, drawdown_pct REAL, account_balance REAL, account_free_margin REAL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class Journal:
    def __init__(self, path: Path = DB, start_equity: float = 5000.0):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        if self.get_meta("start_equity") is None:
            self.set_meta("start_equity", start_equity)
        self.conn.commit()

    # --- meta -------------------------------------------------------------
    def get_meta(self, k: str, default=None):
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, k: str, v) -> None:
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
        self.conn.commit()

    # --- obchody ----------------------------------------------------------
    def open_trade(self, **kw) -> int:
        cols = ",".join(kw)
        q = ",".join("?" * len(kw))
        cur = self.conn.execute(f"INSERT INTO trades({cols}) VALUES({q})",
                                tuple(kw.values()))
        self.conn.commit()
        return cur.lastrowid

    def close_trade(self, tid: int, **kw) -> None:
        sets = ",".join(f"{k}=?" for k in kw)
        self.conn.execute(f"UPDATE trades SET {sets} WHERE id=?",
                          (*kw.values(), tid))
        self.conn.commit()

    def open_positions(self) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM trades WHERE ts_close IS NULL"))

    def traded_today(self, plan_date: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) c FROM trades WHERE plan_date=?",
            (plan_date,)).fetchone()["c"]

    def deviation(self, kind: str, detail: str, plan_date: str = "") -> None:
        self.conn.execute(
            "INSERT INTO deviations(ts,plan_date,kind,detail) VALUES(?,?,?,?)",
            (datetime.now(timezone.utc).timestamp(), plan_date, kind, detail))
        self.conn.commit()

    # --- virtuálna equity -------------------------------------------------
    def realized(self) -> float:
        r = self.conn.execute(
            "SELECT COALESCE(SUM(net_eur),0) s FROM trades "
            "WHERE ts_close IS NOT NULL").fetchone()
        return r["s"]

    def virtual_equity(self, floating: float = 0.0) -> float:
        return float(self.get_meta("start_equity", 5000.0)) + self.realized() + floating

    def hwm(self) -> float:
        r = self.conn.execute("SELECT MAX(hwm) h FROM equity").fetchone()
        base = float(self.get_meta("start_equity", 5000.0))
        return max(r["h"] or base, base)

    def snapshot(self, floating: float, balance: float, free_margin: float) -> dict:
        ve = self.virtual_equity(floating)
        hwm = max(self.hwm(), ve)
        dd = (hwm - ve) / hwm * 100 if hwm > 0 else 0.0
        today = datetime.now(timezone.utc).date().isoformat()
        self.conn.execute(
            "INSERT INTO equity(day,virtual_equity,realized,floating,hwm,"
            "drawdown_pct,account_balance,account_free_margin) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET "
            "virtual_equity=excluded.virtual_equity, realized=excluded.realized,"
            "floating=excluded.floating, hwm=excluded.hwm,"
            "drawdown_pct=excluded.drawdown_pct,"
            "account_balance=excluded.account_balance,"
            "account_free_margin=excluded.account_free_margin",
            (today, ve, self.realized(), floating, hwm, dd, balance, free_margin))
        self.conn.commit()
        return {"virtual_equity": ve, "hwm": hwm, "drawdown_pct": dd}

    def pnl_today(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        r = self.conn.execute(
            "SELECT COALESCE(SUM(net_eur),0) s FROM trades "
            "WHERE ts_close IS NOT NULL AND date(ts_close,'unixepoch')=?",
            (today,)).fetchone()
        return r["s"]

    # --- report §6 --------------------------------------------------------
    def report(self) -> dict:
        rows = list(self.conn.execute(
            "SELECT * FROM trades WHERE ts_close IS NOT NULL"))
        auto = [r for r in rows if not r["manual"]]
        man = [r for r in rows if r["manual"]]

        def block(rs):
            if not rs:
                return {"n": 0}
            rmul = [r["r_multiple"] for r in rs if r["r_multiple"] is not None]
            wins = [r for r in rs if (r["net_eur"] or 0) > 0]
            gp = sum(r["net_eur"] for r in wins)
            gl = -sum(r["net_eur"] for r in rs if (r["net_eur"] or 0) < 0)
            out = {
                "n": len(rs),
                "hit_rate": len(wins) / len(rs) * 100,
                "avg_r": statistics.mean(rmul) if rmul else None,
                "profit_factor": (gp / gl) if gl > 0 else None,
                "net_eur": sum(r["net_eur"] or 0 for r in rs),
            }
            # 95 % interval priemerného R — rozhodovacie pravidlo §6
            if len(rmul) >= 2:
                sd = statistics.stdev(rmul)
                se = sd / (len(rmul) ** 0.5)
                out["r_ci95"] = (out["avg_r"] - 1.96 * se,
                                 out["avg_r"] + 1.96 * se)
            return out

        res = {"automatic": block(auto), "manual": block(man),
               "deviations": self.conn.execute(
                   "SELECT COUNT(*) c FROM deviations").fetchone()["c"]}

        a = res["automatic"]
        if a["n"] >= 100 and a.get("r_ci95") and a["r_ci95"][1] < 0:
            res["verdict"] = ("ZASTAVIŤ — po 100+ obchodoch je horná hranica "
                              "95 % intervalu priemerného R pod nulou")
        elif a["n"] >= 100:
            res["verdict"] = "pokračovať — priemerné R nie je preukázateľne záporné"
        else:
            res["verdict"] = (f"zatiaľ nerozhodnuté — {a['n']}/100 obchodov; "
                              f"do vtedy sa pravidlá NEMENIA (§6)")
        return res
