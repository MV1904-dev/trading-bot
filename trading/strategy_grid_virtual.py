"""Grid stratégie pre virtuálne účty (paper_bot.py).

LiveGrid    — kópia živého Grid25-G2B-CT: rovnaká trieda (trading/
              strategy_grid25.py), kotva na bežiacom extréme, pásmo
              1,12–1,16, kapacita 20+10 a swapová automatika krokov tak,
              ako ju robí bot_ctrader (_apply_swap_regime) — počíta sa zo
              swapových sadzieb live účtu vo feede. Na virtuálnom účte slúži
              ako kontrola: porovnanie s ňou ukáže čistý efekt zmeny.
GridCatchup — LiveGrid + dobeh zmeškaných úrovní (návrh Mariána 16. 10.
              2026). Keď kurz od kotvy prejde naraz k ≥ 2 krokov (gap,
              koniec blackoutu), otvorí sa k pozícií po 10 000 za aktuálnu
              cenu (po strop strany) namiesto jednej, všetky s TP +0,1 %.
              Oproti postupnému gridu je to tá istá expozícia za lepšiu
              cenu; oproti dnešnému botu (jedna pozícia) je to viac pozícií
              hneď po prudkom pohybe. Rátajú sa len úrovne vnútri pásma,
              najviac 6 naraz.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from trading.strategy_base import Bar, Signal
from trading.strategy_grid25 import Grid25, Grid25Config

# Rovnaké hranice ako CTraderBotConfig.SWAP_TIERS (zadanie 3. 9. 2026):
# náklad strany v €/deň → krok; nad poslednou hranicou sa strana neotvára.
SWAP_TIERS = ((0.25, 0.0010), (0.50, 0.0013), (1.00, 0.0015))


def live_grid_config() -> Grid25Config:
    """Konfigurácia ako na serveri: QTY 10 000, 20+10, pásmo 1,12–1,16,
    kotva na extréme. Kroky prepíše swapová automatika."""
    return Grid25Config(qty=10_000, base_levels=20, reserve_levels=10,
                        step_short=0.0010, step_long=0.0015,
                        band_low=1.12, band_high=1.16, anchor="extreme")


class LiveGrid(Grid25):
    id = "GRID"

    def __init__(self, cfg: Optional[Grid25Config] = None):
        super().__init__(cfg or live_grid_config())

    def on_feed(self, feed: dict, mid: float) -> None:
        """Swapová automatika krokov — kópia bot_ctrader._apply_swap_regime."""
        sl, ss = feed.get("swap_long"), feed.get("swap_short")
        if sl is None or ss is None or not mid:
            return
        pip_eur = self.cfg.qty * 0.0001 / mid

        def tier(swap_pips: float) -> Optional[float]:
            cost = max(0.0, -swap_pips) * pip_eur
            for limit, step in SWAP_TIERS:
                if cost <= limit:
                    return step
            return None

        step_l, step_s = tier(sl), tier(ss)
        c = self.cfg
        c.long_enabled = step_l is not None
        c.short_enabled = step_s is not None
        if step_l is not None:
            c.step_long = step_l
        if step_s is not None:
            c.step_short = step_s


class GridCatchup(LiveGrid):
    id = "GRID-DOBEH"
    # Najviac pozícií naraz. Blackout ±30 min býva 0,3–0,6 % pohybu, čo je
    # 3–6 krokov; viac by už nebol zmeškaný vstup, ale nová stávka.
    MAX_CATCHUP = 6

    def _missed(self, side: str, px: float) -> int:
        """Koľko úrovní medzi kotvou a kurzom bolo naozaj povolených.
        Úrovne za hranou pásma sa nerátajú — tie grid vynechal zámerne,
        nie preto, že ich nestihol (inak by návrat do pásma otvoril
        naraz všetko, čo sa nazbieralo mimo neho)."""
        c = self.cfg
        if side == "long":
            top = min(self.ref_long, c.band_high)
            return int(max(0.0, top - px) / (self.ref_long * c.step_long))
        bottom = max(self.ref_short, c.band_low)
        return int(max(0.0, px - bottom) / (self.ref_short * c.step_short))

    def on_bar(self, bar: Bar, atr: Optional[float]) -> list[Signal]:
        out: list[Signal] = []
        for sig in super().on_bar(bar, atr):
            k = self._missed(sig.side, bar.close)
            if k < 2:
                out.append(sig)
                continue
            have = len(self.longs if sig.side == "long" else self.shorts)
            n = max(1, min(k, self.MAX_CATCHUP, self.cfg.cap - have))
            sgn = 1 if sig.side == "long" else -1
            tp = round(bar.close * (1 + sgn * self.cfg.tp_pct), 5)
            for j in range(n):
                out.append(replace(
                    sig, tp_price=tp,
                    reason=f"{sig.reason} | dobeh {j + 1}/{n}",
                    context=dict(sig.context, catchup=n)))
        return out
