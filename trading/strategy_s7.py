"""S7-Cont — „BigMovePullback Continuation“, víťaz strategy_lab_s7.

Myšlienka (z PullbackStudy, Fáza 1): po smerovom pohybe 0,45–0,6 % bez
medzipullbacku > 0,15 % nasleduje v ~62 % prípadov retracement ≥ 38 %,
a **65–67 % takých pohybov po pullbacku POKRAČUJE** za pôvodný extrém.
S7 kupuje toto pokračovanie: vstup v smere pohybu na dne pullbacku,
tesný SL pod dnom, cieľ za pôvodným extrémom. RR hrá v prospech obchodu
(opak S6, ktorý pullback fadoval a na RR ~0,2 zomrel).

⚠️ NEPREŠIEL NEZÁVISLÝM OVERENÍM — modul je preto VYPNUTÝ (S7_ENABLED=False).

Lab (strategy_lab_s7.py) hlásil pre S7_H1_c_tpext OOS +5 724 pri 50k.
Táto čistá reimplementácia generuje na tých istých dátach **bit-identické
vstupy** (cena, SL aj TP sedia na 5 desatinných miest), no pri rovnakých
nákladoch aj veľkosti dáva **−2 747**. Rozdiel nerobí signál, ale to,
ktoré signály sa stihnú vykonať: keď je pozícia obsadená, ďalší setup
prepadne — a už táto drobnosť preklopí výsledok o ~8,5k.

Pri win rate ~49 % a RR ~1 to znamená, že lab číslo bola konkrétna šťastná
postupnosť obchodov, nie robustná prevaha. Robustná stratégia nemení
znamienko podľa poradia vykonaných obchodov.

Modul aj infraštruktúra (H1 bary, stop-loss, časový stop) ostávajú v repe
pre prípadné ďalšie skúmanie: S7 by potreboval buď filter, ktorý vyberá
kvalitnejšie setupy (napr. minimálne RR), alebo možnosť držať viac pozícií
naraz, aby výsledok nezávisel od sekvencovania. Kým sa to nepotvrdí na
nezávislej implementácii, nenasadzovať.

Pravidlá (H1, kauzálne — dno pullbacku sa sleduje priebežne):
* noha zigzagu (reverz 0,15 %) sa uzavrie pivotom → kvalifikácia:
  veľkosť ∈ [0,45 %; 0,60 %], trvanie ≤ 24 h, BEZ news baru počas nohy
  (range ≥ 4× ATR — news pohyby sa podľa Fázy 1 vracajú menej)
* vstup (trigger „c“ z labu): keď retracement od extrému dosiahne ≥ 38 %
  pohybu A následne bar prekoná high (long) / low (short) predchádzajúceho
  baru — čaká sa teda na dôkaz obnoveného momenta
* SL = dno pullbacku ∓ 0,05 % rezerva
* TP = pôvodný extrém + 0,5 × hĺbka pullbacku (rozšírený cieľ)
* časový stop 24 h; setup expiruje po 24 h, alebo keď cena prekoná extrém
  (ušlo nám to) či prepadne za štart nohy (plné otočenie)
* max 1 pozícia a 1 setup naraz

Poznámka k veľkosti: lab preferoval 50k nad 25k len kvôli minimálnej
provízii IBKR ($2/príkaz). Na cTraderi je provízia proporcionálna, takže
malá pozícia nákladovosť nezhoršuje.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from trading.strategy_base import Bar, Signal, StrategyBase


@dataclass
class S7Config:
    qty: float = 2_000
    rev_th: float = 0.0015        # reverzný prah zigzagu (0,15 %)
    size_lo: float = 0.0045       # kvalifikačné okno veľkosti pohybu
    size_hi: float = 0.0060
    max_leg_h: float = 24.0       # max trvanie nohy
    min_retr: float = 0.38        # minimálny retracement pred vstupom
    sl_reserve: float = 0.0005    # rezerva pod dnom (0,05 %)
    tp_ext: float = 0.5           # TP = extrém + 0,5 × hĺbka
    setup_ttl_h: float = 24.0
    max_hold_h: float = 24.0      # časový stop pozície
    news_atr_mult: float = 4.0    # bar s range ≥ 4× ATR = news proxy


class S7Continuation(StrategyBase):
    id = "S7-Cont"
    enabled = True
    timeframe_s = 3600            # H1 — na M5 bola stratégia v labe stratová

    def __init__(self, config: Optional[S7Config] = None):
        self.cfg = config or S7Config()
        # zigzag stav
        self._dir = 0                     # +1 up-noha, -1 down-noha
        self._hi = self._lo = None
        self._hi_t = self._lo_t = 0.0
        self._start_p: Optional[float] = None
        self._start_t = 0.0
        self._ext_p: Optional[float] = None
        self._ext_t = 0.0
        self._leg_news = False
        # setup a pozícia
        self._setup: Optional[dict] = None
        self._prev: Optional[Bar] = None
        self._open_trades: dict[int, str] = {}    # trade_id -> side

    # ------------------------------------------------------------------ #
    def warmup(self, bars: list) -> None:
        """Predohreje zigzag historickými barmi (bez signálov)."""
        for b in bars:
            self._process(b, atr=None, emit=False)

    def restore(self, open_trades: list) -> None:
        for t in open_trades:
            self._open_trades[t["id"]] = t["side"]

    def on_bar(self, bar: Bar, atr: Optional[float]) -> list[Signal]:
        return self._process(bar, atr, emit=True)

    # ------------------------------------------------------------------ #
    def _process(self, bar: Bar, atr: Optional[float], emit: bool) -> list[Signal]:
        cfg = self.cfg
        signals: list[Signal] = []

        # news príznak aktuálnej nohy
        if atr and (bar.high - bar.low) >= cfg.news_atr_mult * atr:
            self._leg_news = True

        # --- 1) živý setup: dno, expirácia, vstup -------------------------
        if self._setup is not None:
            s = self._setup
            d = s["dir"]
            if d == 1:
                s["bottom"] = min(s["bottom"], bar.low)
            else:
                s["bottom"] = max(s["bottom"], bar.high)

            expired = (
                (bar.ts - s["t_ext"]) / 3600 > cfg.setup_ttl_h
                or (d == 1 and (bar.high > s["ext"] or bar.low < s["start"]))
                or (d == -1 and (bar.low < s["ext"] or bar.high > s["start"]))
            )

            if emit and not self._open_trades and self._prev is not None:
                sig = self._try_trigger(bar, s)
                if sig is not None:
                    signals.append(sig)
                    self._setup = None
            if self._setup is not None and expired:
                self._setup = None

        # --- 2) zigzag + kvalifikácia novej nohy ---------------------------
        pivot = self._update_zigzag(bar)
        if pivot and self._setup is None and not self._open_trades:
            move = abs(self._ext_p - self._start_p)
            size = move / self._start_p
            dur_h = (self._ext_t - self._start_t) / 3600
            if (cfg.size_lo <= size <= cfg.size_hi
                    and dur_h <= cfg.max_leg_h and not self._leg_news):
                self._setup = {
                    "dir": pivot, "start": self._start_p, "ext": self._ext_p,
                    "move": move, "t_ext": self._ext_t,
                    "bottom": bar.low if pivot == 1 else bar.high,
                }
        if pivot:
            self._flip(bar, pivot)

        self._prev = bar
        return signals

    # ------------------------------------------------------------------ #
    def _try_trigger(self, bar: Bar, s: dict) -> Optional[Signal]:
        """Trigger „c“: po ≥38 % retracemente prekonanie extrému predošlého baru."""
        cfg = self.cfg
        d = s["dir"]
        depth = (s["ext"] - s["bottom"]) if d == 1 else (s["bottom"] - s["ext"])
        if depth < cfg.min_retr * s["move"]:
            return None

        if d == 1:
            if bar.high <= self._prev.high:
                return None
            entry = max(self._prev.high, bar.open)
            sl = s["bottom"] * (1 - cfg.sl_reserve)
            tp = s["ext"] + cfg.tp_ext * depth
            side = "long"
            if not (sl < entry < tp):
                return None
        else:
            if bar.low >= self._prev.low:
                return None
            entry = min(self._prev.low, bar.open)
            sl = s["bottom"] * (1 + cfg.sl_reserve)
            tp = s["ext"] - cfg.tp_ext * depth
            side = "short"
            if not (tp < entry < sl):
                return None

        risk = abs(entry - sl)
        rr = abs(tp - entry) / risk if risk > 0 else 0.0
        return Signal(
            strategy_id=self.id, side=side, qty=cfg.qty,
            tp_price=round(tp, 5), sl_price=round(sl, 5),
            max_hold_s=cfg.max_hold_h * 3600,
            reason=(f"pokračovanie po pullbacku {100 * depth / s['move']:.0f} % "
                    f"(pohyb {100 * s['move'] / s['start']:.2f} %), "
                    f"breakout {self._prev.high if d == 1 else self._prev.low:.5f}, "
                    f"RR {rr:.1f}"),
            context={"leg_start": s["start"], "leg_ext": s["ext"],
                     "pb_bottom": s["bottom"], "depth_pct": depth / s["move"],
                     "rr": rr, "entry_hint": round(entry, 5)},
        )

    def _update_zigzag(self, bar: Bar) -> int:
        """Vráti +1/-1 ak sa práve uzavrela up/down noha, inak 0."""
        th = self.cfg.rev_th
        if self._dir == 0:
            self._hi = bar.high if self._hi is None else max(self._hi, bar.high)
            self._lo = bar.low if self._lo is None else min(self._lo, bar.low)
            if self._hi - bar.low > self._hi * th:
                self._dir, self._start_p, self._start_t = -1, self._hi, bar.ts
                self._ext_p, self._ext_t = bar.low, bar.ts
                self._leg_news = False
            elif bar.high - self._lo > self._lo * th:
                self._dir, self._start_p, self._start_t = 1, self._lo, bar.ts
                self._ext_p, self._ext_t = bar.high, bar.ts
                self._leg_news = False
            return 0

        if self._dir == 1:
            if bar.high > self._ext_p:
                self._ext_p, self._ext_t = bar.high, bar.ts
            if self._ext_p - bar.low > self._ext_p * th:
                return 1
        else:
            if bar.low < self._ext_p:
                self._ext_p, self._ext_t = bar.low, bar.ts
            if bar.high - self._ext_p > self._ext_p * th:
                return -1
        return 0

    def _flip(self, bar: Bar, pivot: int) -> None:
        self._start_p, self._start_t = self._ext_p, self._ext_t
        if pivot == 1:
            self._dir = -1
            self._ext_p, self._ext_t = bar.low, bar.ts
        else:
            self._dir = 1
            self._ext_p, self._ext_t = bar.high, bar.ts
        self._leg_news = False

    # ------------------------------------------------------------------ #
    def on_trade_opened(self, trade_id: int, side: str, price: float) -> None:
        self._open_trades[trade_id] = side
        self._setup = None

    def on_trade_closed(self, trade_id: int, side: str, price: float) -> None:
        self._open_trades.pop(trade_id, None)

    def status_line(self) -> str:
        st = "—"
        if self._setup is not None:
            s = self._setup
            d = s["dir"]
            depth = (s["ext"] - s["bottom"]) if d == 1 else (s["bottom"] - s["ext"])
            st = (f"{'UP' if d == 1 else 'DN'} pohyb "
                  f"{100 * s['move'] / s['start']:.2f} %, pullback "
                  f"{100 * depth / s['move']:.0f} %")
        return (f"{self.id}: {'ON' if self.enabled else 'OFF'} (H1) | "
                f"pozícia {len(self._open_trades)}/1 | setup: {st}")
