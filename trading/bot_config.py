"""Konfigurácia bota.

POZOR: MODE je natvrdo "paper". Prepnutie na live vyžaduje VEDOMÚ zmenu:
MODE="live" TU v kóde + premennú prostredia BOT_CONFIRM_LIVE presne
"ROZUMIEM-RIZIKU" + live port. Bot inak odmietne štart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class BotConfig:
    # --- režim (paper natvrdo) ------------------------------------------
    MODE: str = "paper"            # "paper" | "live" — live vyžaduje potvrdenie
    HOST: str = "127.0.0.1"
    PORT: int = 4002               # 4002 = Gateway paper, 7497 = TWS paper
    CLIENT_ID: int = 7

    # --- trh a dáta ------------------------------------------------------
    PAIR: str = "EURUSD"
    BAR_SECONDS: int = 300         # M5
    ATR_PERIOD: int = 14
    TICK_SECONDS: float = 10.0     # kadencia hlavnej slučky
    DATA_GAP_ALARM_S: int = 300    # výpadok dát/Gateway > 5 min → pauza+alarm

    # --- Plus500 signálny režim ------------------------------------------
    # Bot obchoduje na IBKR paper; navyše pri každom otvorení/zatvorení
    # pošle Telegram „signál“ na ručné zrkadlenie v Plus500 appke.
    P500_SIGNALS: bool = True
    P500_SIGNAL_QTY: float = 10_000   # čiastka pre P500 (menší reálny účet)

    # --- Telegram / briefing --------------------------------------------
    TIMEZONE: str = "Europe/Bratislava"
    BRIEFING_HOUR: int = 8
    BRIEFING_HOUR_END: int = 10    # po tomto okne sa briefing už neposiela
    BAND_ALERT_LOW: float = 1.1250
    BAND_ALERT_HIGH: float = 1.1550
    BAND_ALERT_RESET: float = 0.0030    # hystéréza na reset alarmu
    DD_ALARM_PCT: float = 10.0          # floating DD > 10 % kapitálu → alarm

    # --- súbory ----------------------------------------------------------
    DB_PATH: Path = field(default_factory=lambda: ROOT / "data" / "bot.db")
    CALENDAR_CACHE: Path = field(
        default_factory=lambda: ROOT / "data" / "ff_calendar.json")
    LOG_PATH: Path = field(default_factory=lambda: ROOT / "data" / "bot.log")
