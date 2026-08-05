#!/usr/bin/env python3
"""Scenario engine + risk overlay (§3, §4 zadania).

Skeleton scenára je deterministický: entry zóna, SL a TP vyplývajú
z úrovní L3 a z ATR. LLM sa tu zámerne nepoužíva — nemá vymýšľať cenové
úrovne, to je práca dát. Jeho miesto je L5 (klasifikácia správ) a
narativ, nie čísla.

Tvrdé pravidlá zo zadania, ktoré kód vynucuje a nedá sa ich obísť:
* SL minimálne 0,8 × ATR(14) D1 od vstupu, nikdy tesnejšie
* TP1 aspoň RR 1,2, inak scenár neexistuje
* entry zóna max 20 pipov
* risk 0,5 % equity, strop efektívnej páky 5×
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from daily_plan.context import Event, Macro, Structure, Zone

PIP = 0.0001
ENTRY_ZONE_MAX_PIPS = 20
SL_MIN_ATR = 0.8
TP1_MIN_RR = 1.2
TP2_MIN_RR = 2.0
RISK_PCT = 0.005
LEV_CAP = 5.0
VOLUME_STEP = 1000.0        # broker berie len násobky 0,01 lotu (§broker)


@dataclass
class Scenario:
    tag: str                 # P | A1 | A2
    side: str | None         # buy | sell | None
    kind: str                # pullback | breakout | rejection | no-trade
    trigger: str
    entry_lo: float | None = None
    entry_hi: float | None = None
    sl: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    rr1: float | None = None
    rr2: float | None = None
    time_stop_days: int = 3
    invalidation: list[str] = field(default_factory=list)
    note: str = ""
    volume: float | None = None
    risk_eur: float | None = None

    @property
    def valid(self) -> bool:
        return self.side is not None and self.rr1 is not None \
            and self.rr1 >= TP1_MIN_RR


def _pick_entry(zones: list[Zone], price: float, side: str) -> Zone | None:
    """Najbližšia zóna v smere, z ktorej sa dá vstúpiť."""
    cand = [z for z in zones if 5 <= z.dist_pips(price) <= 120]
    if not cand:
        return None
    # silnejšia zóna vyhráva, pri rovnosti bližšia
    return sorted(cand, key=lambda z: (-z.strength, z.dist_pips(price)))[0]


def _zone_side(side: str, kind: str, sup: list[Zone], res: list[Zone]
               ) -> list[Zone]:
    """Z ktorej strany sa vstupuje.

    Pullback aj rejection nakupujú na supporte a predávajú na rezistencii.
    Breakout je opačný: nakupuje sa AŽ ZA rezistenciou a predáva ZA
    supportom. Bez tohto rozlíšenia by A1 „breakout buy" kupoval na
    supporte pod cenou, čo je odraz, nie prienik — a jeho trigger
    (H1 close nad zónou) by bol už dávno splnený.
    """
    if kind == "breakout":
        return res if side == "buy" else sup
    return sup if side == "buy" else res


def _build_directional(side: str, kind: str, price: float, atr_d1: float,
                       sup: list[Zone], res: list[Zone], tag: str) -> Scenario:
    entry_zone = _pick_entry(_zone_side(side, kind, sup, res), price, side)
    if entry_zone is None:
        return Scenario(tag, None, kind, "žiadna vhodná zóna v dosahu",
                        note="scenár neexistuje — v okolí ceny nie je zóna "
                             "medzi 5 a 120 pipmi")

    lo, hi = entry_zone.low, entry_zone.high
    if (hi - lo) / PIP > ENTRY_ZONE_MAX_PIPS:
        # zúžime na okraj, ktorý cena narazí skôr
        if side == "buy":
            lo = hi - ENTRY_ZONE_MAX_PIPS * PIP
        else:
            hi = lo + ENTRY_ZONE_MAX_PIPS * PIP
    entry = (lo + hi) / 2

    # SL za štruktúrou, minimálne 0,8 × ATR — vždy ten VZDIALENEJŠÍ
    struct_sl = lo - 10 * PIP if side == "buy" else hi + 10 * PIP
    min_sl = entry - SL_MIN_ATR * atr_d1 if side == "buy" \
        else entry + SL_MIN_ATR * atr_d1
    sl = min(struct_sl, min_sl) if side == "buy" else max(struct_sl, min_sl)
    risk = abs(entry - sl)

    # TP na protizóny. Vyberá sa NAJSILNEJŠIA zóna spĺňajúca RR, nie prvá
    # v ceste — prvá vyhovujúca vedela poslať TP2 na slabú zónu tesne pred
    # najsilnejšou úrovňou plánu (sila 2 pred silou 6, 3. 8. 2026).
    opposite = res if side == "buy" else sup
    cands = []
    for z in opposite:
        target = z.mid
        rr = (target - entry) / risk if side == "buy" else (entry - target) / risk
        if rr > 0:
            cands.append((z, target, rr))
    tp1 = tp2 = rr1 = rr2 = None
    obstacles: list = []
    c1 = [c for c in cands if c[2] >= TP1_MIN_RR]
    if c1:
        z1, tp1, rr1 = max(c1, key=lambda c: (c[0].strength, -c[2]))
        # prekážky: zóny so silou ≥ 2 medzi vstupom a TP1, ktoré samy RR
        # nespĺňajú — cieľ ich nevymaže, cena cez ne musí prejsť
        obstacles = [c[0] for c in cands
                     if c[2] < rr1 and c[0].strength >= 2 and c[0] is not z1
                     and c[2] < TP1_MIN_RR]
        c2 = [c for c in cands if c[2] >= max(TP2_MIN_RR, rr1 + 0.1)]
        if c2:
            _, tp2, rr2 = max(c2, key=lambda c: (c[0].strength, -c[2]))

    inval = [f"denný close za {sl:.5f} pred aktiváciou",
             "T1 udalosť v okne pred vstupom",
             f"gap cez celú zónu {lo:.5f}–{hi:.5f}"]

    trig = {
        "pullback": f"limit vstup pri návrate do zóny {lo:.5f}–{hi:.5f}",
        "breakout": (f"H1 close nad {hi:.5f}" if side == "buy"
                     else f"H1 close pod {lo:.5f}"),
        "rejection": f"H1 close späť do zóny {lo:.5f}–{hi:.5f} po preniknutí",
    }[kind]

    note = (f"zóna sila {entry_zone.strength}: "
            f"{', '.join(entry_zone.sources[:3])}")
    if obstacles:
        note += (" | POZOR: cesta k TP1 vedie cez "
                 + "; ".join(f"{z.low:.5f}–{z.high:.5f} (sila {z.strength})"
                             for z in obstacles[:2]))
    return Scenario(tag, side, kind, trig, lo, hi, sl, tp1, tp2, rr1, rr2,
                    invalidation=inval, note=note)


def vote_bias(macro: Macro, structure: Structure, news_bias: str | None) -> str:
    """L1 + L2 + L5 hlasovanie. Pri remíze rozhoduje štruktúra —
    tá je merateľná, makro a správy sú interpretácia."""
    votes = []
    if macro.bias != "neutral":
        votes.append("buy" if macro.bias == "EUR+" else "sell")
    if structure.bias != "mixed":
        votes.append("buy" if structure.bias == "bullish" else "sell")
    if news_bias in ("EUR+", "USD+"):
        votes.append("buy" if news_bias == "EUR+" else "sell")
    if not votes:
        return "none"
    b, s = votes.count("buy"), votes.count("sell")
    if b == s:
        return "buy" if structure.bias == "bullish" else "sell" \
            if structure.bias == "bearish" else "none"
    return "buy" if b > s else "sell"


def no_trade_reasons(events: list[Event], atr_d1: float, atr_avg: float,
                     macro: Macro, structure: Structure, day: date) -> list[str]:
    out = []
    t1_today = [e for e in events
                if e.tier == 1 and e.ts.date() == day]
    if t1_today:
        out.append("T1 udalosť v deň obchodovania: "
                   + ", ".join(f"{e.ts:%H:%M} {e.title}" for e in t1_today[:3]))
    if atr_avg and atr_d1 > 1.5 * atr_avg:
        out.append(f"ATR spike {atr_d1/PIP:.0f} p proti priemeru "
                   f"{atr_avg/PIP:.0f} p (> 1,5×)")
    conflict = ((macro.bias == "EUR+" and structure.bias == "bearish")
                or (macro.bias == "USD+" and structure.bias == "bullish"))
    if conflict:
        out.append(f"konflikt L1 ({macro.bias}) vs L2 ({structure.bias})")
    if day.weekday() == 4:
        out.append("piatok — pozícia by musela byť flat do 19:00 UTC")
    return out


def quantize_volume(units: float) -> float:
    """Objem zaokrúhlený NADOL na krok brokera.

    Nadol zámerne: nahor by objem prekročil riziko 0,5 %. Bez tohto kroku
    vychádzali z rizikového výpočtu objemy ako 5803, ktoré broker odmieta
    (TRADING_BAD_VOLUME) — plán potom nešlo vôbec vykonať.
    """
    return float(int(units // VOLUME_STEP) * int(VOLUME_STEP))


def size(scn: Scenario, equity: float, price: float) -> Scenario:
    """Objem z rizika 0,5 % a vzdialenosti entry→SL, so stropom páky."""
    if not scn.valid or scn.sl is None:
        return scn
    entry = (scn.entry_lo + scn.entry_hi) / 2
    risk_price = abs(entry - scn.sl)
    if risk_price <= 0:
        return scn
    risk_eur = equity * RISK_PCT
    units = risk_eur / risk_price
    max_units = equity * LEV_CAP
    raw = min(units, max_units)
    scn.volume = quantize_volume(raw)
    if scn.volume < VOLUME_STEP:
        # Objem 0 scenár fakticky vyradí: executor ho odmietne (volume_guard)
        # a emit ho vykreslí ako „—". `valid` je property, nedá sa nastaviť.
        scn.risk_eur = 0.0
        scn.note += (f" | riziko {risk_eur:.2f} € pri SL {risk_price/PIP:.0f} p "
                     f"vychádza pod minimálny objem {VOLUME_STEP:.0f} — "
                     f"scenár sa nedá zobchodovať")
        return scn
    scn.risk_eur = scn.volume * risk_price
    if units > max_units:
        scn.note += (f" | objem orezaný stropom páky {LEV_CAP}× "
                     f"(riziko klesá na {scn.risk_eur:.2f} €)")
    if raw - scn.volume >= 1:
        scn.note += (f" | objem {raw:.0f} → {scn.volume:.0f} "
                     f"(krok brokera {VOLUME_STEP:.0f})")
    return scn


def build(price: float, atr_d1: float, atr_avg: float, macro: Macro,
          structure: Structure, sup: list[Zone], res: list[Zone],
          events: list[Event], equity: float, day: date,
          news_bias: str | None = None) -> list[Scenario]:
    reasons = no_trade_reasons(events, atr_d1, atr_avg, macro, structure, day)
    direction = vote_bias(macro, structure, news_bias)
    # Na okraji pásma rozhoduje, či smer súhlasí s prielomom toho okraja:
    # na dolnom kraji je prielom smerom nadol, takže sell = breakout
    # a buy = odraz (rejection). Bez toho by sa na 10. percentile
    # ročného rozsahu predávalo „rejection" na rezistencii, čo je fade
    # proti vlastnému biasu.
    if structure.logic == "range":
        kind = "pullback"
    elif structure.location == "dolný kraj":
        kind = "breakout" if direction == "sell" else "rejection"
    elif structure.location == "horný kraj":
        kind = "breakout" if direction == "buy" else "rejection"
    else:
        kind = "pullback"

    if direction == "none":
        p = Scenario("P", None, "no-trade", "bias sa nezhodol",
                     note="L1, L2 aj L5 dali dokopy remízu bez rozhodujúcej "
                          "štruktúry — primárny scenár neexistuje")
        a1 = Scenario("A1", None, "no-trade", "—")
    else:
        p = size(_build_directional(direction, kind, price, atr_d1, sup, res, "P"),
                 equity, price)
        opp = "sell" if direction == "buy" else "buy"
        a1 = size(_build_directional(opp, "breakout", price, atr_d1,
                                     sup, res, "A1"), equity, price)

    a2 = Scenario("A2", None, "no-trade",
                  "platí, ak nastane ktorákoľvek podmienka",
                  invalidation=reasons or ["žiadna podmienka no-trade dnes"],
                  note="pri splnení sa P aj A1 rušia")
    return [p, a1, a2]
