"""Register virtuálnych účtov — čo beží v paper_bot.py.

Nová stratégia na porovnanie = nový záznam tu + nasadenie
(scripts/paper_deploy.sh). Každý účet má vlastnú SQLite v data/paper/,
vlastný env v Supabase (dashboard ho nájde sám) a rovnaký štartovací
kapitál. Všetky bežia na tom istom feede zo živého bota, takže sa líšia
len stratégiou.

Pravidlá, aby porovnanie dávalo zmysel:
* env a db sa po prvom štarte nemenia — sú to kľúče histórie,
* stratégiu, ktorá už beží, nemeníme; zmena = nový záznam s novým env,
* env musí začínať „paper" (dashboard podľa toho rozlišuje virtuálne účty).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from trading.strategy_base import StrategyBase
from trading.strategy_grid_virtual import GridCatchup, LiveGrid
from trading.strategy_guard import GuardConfig, GuardGrid


@dataclass(frozen=True)
class Virtual:
    env: str                              # Supabase env, začína „paper"
    name: str                             # meno v dashboarde
    db: str                               # súbor v data/paper/
    make: Callable[[], StrategyBase]
    note: str = ""


VIRTUALS: list[Virtual] = [
    Virtual("paper", "Strážca", "paper.db", lambda: GuardGrid(GuardConfig()),
            "grid s trendovou poistkou (H1 EMA 100/400), od 16. 10. 2026"),
    Virtual("paper-grid", "Grid (kópia live)", "grid.db", LiveGrid,
            "rovnaký grid ako live — kontrola pre ostatné varianty, od 17. 10. 2026"),
    Virtual("paper-dobeh", "Grid + dobeh", "grid_dobeh.db", GridCatchup,
            "live grid, pri skoku o k krokov otvorí k pozícií naraz, od 17. 10. 2026"),
]
