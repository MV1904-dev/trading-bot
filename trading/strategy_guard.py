"""Strážca (GUARD) — obojstranný grid s trendovou poistkou.

Súper živého Grid25-G2B-CT na virtuálnom účte (zadanie 16. 10. 2026).
Motor je rovnaký — vstupy po kroku od kotvy, TP +0.1 %, bez SL — ale
dnešnému gridu chýba jediná vec: nevie, kedy prestať prikupovať proti
pohybu, ktorý ho potápa. Strážca to rieši trendom na H1:

* trend = EMA(100) vs EMA(400) na H1 zatváracích cenách (~4 a ~17 dní)
* strana v smere trendu: krok 0.10 %, strop 20 pozícií
* strana proti trendu: krok 0.20 %, strop 4 pozície — grid tam ďalej
  zbiera cykly, ale nehromadí stratu
* kotva = bežiace maximum (long) / minimum (short), po vstupe cena vstupu
  — tak, ako dnes reálne beží live grid
* long navyše len pri poklese ≥ 2× ATR(14, M5), pri gape ≥ 2 krokov TP
  na najbližšiu preskočenú úroveň (G2B)
* pásmo 1.12–1.16 rovnako ako live: pod dolnou hranou žiadne shorty,
  nad hornou žiadne longy
* strážca marže: pod `guard_ml` % zavrie najstratovejšiu pozíciu skôr, než
  to pri 50 % urobí broker (v labe sa za 2 roky nespustil ani raz)

Lab (strategy_lab_vs.py, 10/2024–10/2026, kapitál 15 200 €, verný model
cTradera — hedging marže, stop-out 50 %, swap, provízia, spread):

                    výsledok  max prepad  min marža  stop-out
    G2B live         +21 057     12 570       49 %     11×
    Strážca          +24 628      7 732      151 %      0×
    live obdobie     −2 017 vs +773 (od 6. 8. 2026)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from trading.strategy_base import Bar, Signal, StrategyBase


@dataclass
class GuardConfig:
    qty: float = 10_000.0
    step: float = 0.0010          # krok v smere trendu
    step_ct: float = 0.0020       # krok proti trendu
    tp_pct: float = 0.001
    cap: int = 20                 # strop strany v smere trendu
    cap_ct: int = 4               # strop strany proti trendu
    atr_mult: float = 2.0
    band_low: float = 1.12
    band_high: float = 1.16
    ema_fast: int = 100           # H1
    ema_slow: int = 400           # H1
    guard_ml: float = 110.0       # vlastný stop-out nad brokerovým (50 %)


class _Ema:
    def __init__(self, n: int):
        self.k = 2 / (n + 1)
        self.v: Optional[float] = None

    def add(self, x: float) -> None:
        self.v = x if self.v is None else self.v + self.k * (x - self.v)


class GuardGrid(StrategyBase):
    id = "GUARD"
    enabled = True
    timeframe_s = 300

    def __init__(self, cfg: Optional[GuardConfig] = None):
        self.cfg = cfg or GuardConfig()
        self.fast = _Ema(self.cfg.ema_fast)
        self.slow = _Ema(self.cfg.ema_slow)
        self.h1_n = 0
        self._h1: Optional[list] = None          # [hodina, close] tvoriaceho sa baru
        self.longs: dict[int, float] = {}
        self.shorts: dict[int, float] = {}
        self.ref_long: Optional[float] = None
        self.ref_short: Optional[float] = None
        self.last_long = 0.0
        self.last_short = 0.0

    # --- trend ----------------------------------------------------------------
    def _h1_close(self, close: float) -> None:
        self.fast.add(close)
        self.slow.add(close)
        self.h1_n += 1

    def _feed_h1(self, bar: Bar) -> None:
        """H1 zatváracie ceny z M5 barov (hodina sa uzavrie prvým barom
        ďalšej hodiny — rovnako ako v labe)."""
        hour = int(bar.ts // 3600)
        if self._h1 is not None and self._h1[0] != hour:
            self._h1_close(self._h1[1])
        self._h1 = [hour, bar.close]

    def trend(self) -> int:
        """+1 uptrend, −1 downtrend, 0 = ešte nezahriaty."""
        if self.h1_n < self.cfg.ema_slow // 2 or self.fast.v is None:
            return 0
        return 1 if self.fast.v > self.slow.v else -1

    # --- StrategyBase ---------------------------------------------------------
    def warmup(self, bars: list) -> None:
        """bars = uzavreté H1 sviečky (najstaršia prvá)."""
        for b in bars:
            self._h1_close(b.close)

    def restore(self, open_trades: list) -> None:
        for r in open_trades:
            (self.longs if r["side"] == "long" else self.shorts)[r["id"]] = r["entry_price"]

    def on_trade_opened(self, trade_id: int, side: str, price: float) -> None:
        if side == "long":
            self.longs[trade_id] = price
            self.last_long = self.ref_long = price
        else:
            self.shorts[trade_id] = price
            self.last_short = self.ref_short = price

    def on_trade_closed(self, trade_id: int, side: str, price: float) -> None:
        (self.longs if side == "long" else self.shorts).pop(trade_id, None)

    def on_bar(self, bar: Bar, atr: Optional[float]) -> list[Signal]:
        self._feed_h1(bar)
        c = self.cfg
        h, l, px = bar.high, bar.low, bar.close
        self.ref_long = max(self.ref_long or px, h)
        self.ref_short = min(self.ref_short or px, l)
        if atr is None:
            return []
        tr = self.trend()
        out: list[Signal] = []
        for side in ("long", "short"):
            n = len(self.longs if side == "long" else self.shorts)
            with_trend = tr >= 0 if side == "long" else tr <= 0
            cap = c.cap if with_trend else c.cap_ct
            step = c.step if with_trend else c.step_ct
            if n >= cap:
                continue
            if side == "long" and px >= c.band_high:
                continue
            if side == "short" and px <= c.band_low:
                continue
            ref = self.ref_long if side == "long" else self.ref_short
            move = ref - px if side == "long" else px - ref
            need = ref * step
            if side == "long":
                need = max(need, c.atr_mult * atr)
            last = self.last_long if side == "long" else self.last_short
            unlock = n < 20 or abs(px - last) > c.atr_mult * atr
            if move < need or not unlock:
                continue
            gap = int(move / (ref * step)) >= 2
            if side == "long":
                tp = px * (1 + step) if gap else px * (1 + c.tp_pct)
            else:
                tp = px * (1 - step) if gap else px * (1 - c.tp_pct)
            out.append(Signal(
                strategy_id=self.id, side=side, qty=c.qty,
                tp_price=round(tp, 5),
                reason=(f"{'pokles' if side == 'long' else 'rast'} {move:.5f} "
                        f"od kotvy {ref:.5f}, trend {'↑' if tr > 0 else '↓' if tr < 0 else '?'}"
                        f"{'' if with_trend else ' (proti trendu)'}"
                        + (" | GAP → TP na úroveň" if gap else "")),
                context={"ref": ref, "atr": atr, "trend": tr, "levels": n,
                         "with_trend": with_trend, "step": step,
                         "ema_fast": self.fast.v, "ema_slow": self.slow.v},
            ))
        return out

    def status_line(self) -> str:
        tr = {1: "↑", -1: "↓", 0: "?"}[self.trend()]
        return (f"Strážca: trend {tr} | long {len(self.longs)}, "
                f"short {len(self.shorts)} | ref_L {self.ref_long or 0:.5f} "
                f"ref_S {self.ref_short or 0:.5f}")
