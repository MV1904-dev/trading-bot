#!/usr/bin/env python3
"""Ľudské vysvetlenie plánu.

Generuje sa deterministicky z tých istých dát, z ktorých vznikol plán —
žiadny LLM. Text tak popisuje skutočnú úvahu enginu, nie dodatočnú
interpretáciu, a nemôže sa od plánu rozísť.
"""

from __future__ import annotations

PIP = 0.0001


def _pct_text(p: float | None) -> str:
    if p is None:
        return ""
    if p <= 20:
        kde = "v dolnej pätine"
    elif p <= 40:
        kde = "v dolnej polovici"
    elif p < 60:
        kde = "približne v strede"
    elif p < 80:
        kde = "v hornej polovici"
    else:
        kde = "v hornej pätine"
    return f"{kde} ročného rozsahu ({p:.0f}. percentil)"


def _fmt(x) -> str:
    return f"{float(x):.4f}".rstrip("0").rstrip(".")


def build_narrative(plan: dict) -> str:
    c = plan["context"]
    m, st = c["L1_macro"], c["L2_structure"]
    price = plan["price_at_build"]
    scns = {s["tag"]: s for s in plan["scenarios"]}
    p, a1 = scns.get("P"), scns.get("A1")

    out: list[str] = []

    # --- situácia ---------------------------------------------------------
    bias_txt = {"bullish": "skôr rastie", "bearish": "skôr klesá",
                "mixed": "nemá jasný smer"}.get(st["bias"], "")
    veta = f"Euro voči doláru stojí na {_fmt(price)}"
    pct = _pct_text(st.get("pct_1y"))
    if pct:
        veta += f", {pct}"
    if bias_txt:
        veta += f", a celkovo {bias_txt}"
    out.append(veta + ".")

    if m["bias"] == "neutral":
        out.append("Úrokové sadzby, ktoré hýbu menami dlhodobo, sa za "
                   "posledný týždeň nepohli — dnes teda smer nenapovedajú.")
    else:
        kto = "eura" if m["bias"] == "EUR+" else "dolára"
        out.append(f"Úrokové sadzby sa za týždeň pohli v prospech {kto} "
                   f"({m['reason']}).")

    # --- kľúčové miesto ---------------------------------------------------
    if p and p.get("side"):
        zdroje = p.get("note", "").split("|")[0].replace("zóna sila", "sila")
        smer_zony = "nad" if p["side"] == "sell" else "pod"
        out.append(
            f"Kúsok {smer_zony} dnešnou cenou, medzi {_fmt(p['entry_lo'])} "
            f"a {_fmt(p['entry_hi'])}, je miesto, kde sa cena v minulosti "
            f"opakovane zastavila ({zdroje.strip()}). Čím viac takých vecí "
            f"sa zíde na jednom mieste, tým väčšia šanca, že cena tam "
            f"naozaj zareaguje.")

        # --- dve odpovede -------------------------------------------------
        out.append("Plán je jedna otázka: čo spraví cena, keď na to miesto "
                   "príde? Nikto to nevie dopredu, tak sú pripravené obe "
                   "odpovede:")

        akcia = "predá" if p["side"] == "sell" else "kúpi"
        odraz = "odrazí sa od nej nadol" if p["side"] == "sell" \
            else "odrazí sa od nej nahor"
        out.append(
            f"• Možnosť P — cena príde k tej úrovni a {odraz}. Bot tam "
            f"{akcia} {p['volume']:,.0f} jednotiek. Prvé zisky sa berú pri "
            f"{_fmt(p['tp1'])}" + (f", zvyšok pri {_fmt(p['tp2'])}" if p.get("tp2")
                                   else "")
            + f". Ak sa plán mýli, stráca — ale najviac "
              f"{p['risk_eur']:.2f} €, tam je záchranná brzda.")
        if "POZOR" in p.get("note", ""):
            out.append("  Pozor: " + p["note"].split("POZOR:")[1].strip()
                       + " — cena cez ňu musí prejsť, môže sa tam zaseknúť.")

        if a1 and a1.get("side"):
            akcia2 = "kúpi" if a1["side"] == "buy" else "predá"
            out.append(
                f"• Možnosť A1 — cena úroveň prerazí ({a1['trigger']}). "
                f"Padnutá prekážka často znamená ďalší pohyb tým smerom, "
                f"tak bot {akcia2}. Ciele {_fmt(a1['tp1'])}"
                + (f" a {_fmt(a1['tp2'])}" if a1.get("tp2") else "")
                + f", rovnaká maximálna strata {a1['risk_eur']:.2f} €.")
            if "POZOR" in a1.get("note", ""):
                out.append("  Pozor: " + a1["note"].split("POZOR:")[1].strip()
                           + ".")

        out.append("Aktivuje sa len jedna z nich — ktorá nastane prvá, tá "
                   "druhá sa ruší. A pokojne ani jedna: ak cena k tomu "
                   "miestu nedôjde, deň prejde bez obchodu a to je v "
                   "poriadku.")
    else:
        out.append("Dnes žiadny obchodovateľný scenár — podmienky sa "
                   "nezhodli na smere alebo v dosahu nie je použiteľná "
                   "úroveň.")

    # --- poctivý záver ----------------------------------------------------
    out.append(
        "Toto nie je predpoveď. Plán netvrdí, že vie, kam cena pôjde — "
        "hovorí len, kde je zaujímavé miesto, aké sú dve rozumné reakcie "
        "a aká je vopred podpísaná maximálna strata. Hodnota sa ukáže až "
        "po ~100 takýchto dňoch v denníku.")

    ev = c.get("L4_events", [])
    t2 = [e for e in ev if e["tier"] == 2 and e["ts"][:10] == plan["plan_date"]]
    if t2:
        out.append("Dnes vychádzajú dáta ("
                   + ", ".join(f"{e['ts'][11:16]} {e['title']}" for e in t2[:2])
                   + ") — pol hodiny okolo nich bot nevstupuje.")

    return "\n\n".join(out)
