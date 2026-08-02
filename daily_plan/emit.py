#!/usr/bin/env python3
"""Plan emit (§5) — JSON pre executor, MD pre človeka.

Plán je po zapísaní nemenný. Súbor sa pomenúva dátumom obchodného dňa
a ak už existuje, emit ZLYHÁ namiesto prepísania — spätná úprava je
podľa §5 zakázaná a ticho prepísaný plán by znehodnotil celý denník.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from daily_plan.context import Event, Macro, Structure, Zone
from daily_plan.scenarios import Scenario

PIP = 0.0001


def _zone_line(z: Zone, price: float) -> str:
    return (f"{z.low:.5f}–{z.high:.5f} | sila {z.strength} | "
            f"{z.dist_pips(price):.0f} p | {', '.join(z.sources[:3])}")


def to_json(day: date, price: float, atr_d1: float, macro: Macro,
            structure: Structure, sup: list[Zone], res: list[Zone],
            events: list[Event], scenarios: list[Scenario],
            equity: float, news: list[dict] | None) -> dict:
    return {
        "plan_date": day.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": "EURUSD",
        "price_at_build": price,
        "atr_d1_pips": round(atr_d1 / PIP, 1),
        "equity": equity,
        "context": {
            "L1_macro": asdict(macro),
            "L2_structure": {k: v for k, v in asdict(structure).items()},
            "L3_support": [{"low": z.low, "high": z.high,
                            "strength": z.strength, "sources": z.sources}
                           for z in sup[:6]],
            "L3_resistance": [{"low": z.low, "high": z.high,
                               "strength": z.strength, "sources": z.sources}
                              for z in res[:6]],
            "L4_events": [{"ts": e.ts.isoformat(), "currency": e.currency,
                           "title": e.title, "tier": e.tier} for e in events],
            "L5_news": news or [],
        },
        "scenarios": [asdict(s) for s in scenarios],
    }


def to_md(plan: dict) -> str:
    c = plan["context"]
    m, s = c["L1_macro"], c["L2_structure"]
    price = plan["price_at_build"]
    out = [
        f"# Denný plán EURUSD — {plan['plan_date']}",
        "",
        f"Zostavené {plan['generated_at'][:16]} UTC pri cene "
        f"{price:.5f}, ATR(14) D1 {plan['atr_d1_pips']:.0f} pipov, "
        f"equity {plan['equity']:,.2f} €.",
        "",
        "## Kontext",
        "",
        f"- **L1 makro** — {m['bias']}: {m['reason']}"
        + ("  \n  _(čiastočné: 2Y výnosy US−DE zatiaľ nie sú napojené)_"
           if m.get("partial") else ""),
        f"- **L2 štruktúra** — {s['bias']}, cena na {s['location']} "
        f"→ logika {s['logic']}; percentil 1R "
        f"{s['pct_1y']:.0f} %" if s.get("pct_1y") is not None else
        f"- **L2 štruktúra** — {s['bias']}",
    ]
    for z, lbl in ((c["L3_resistance"], "rezistencie"), (c["L3_support"], "supporty")):
        if z:
            top = sorted(z, key=lambda x: -x["strength"])[:3]
            out.append(f"- **L3 {lbl}** — " + " · ".join(
                f"{x['low']:.5f}–{x['high']:.5f} (sila {x['strength']})"
                for x in top))
    ev = c["L4_events"]
    out.append(f"- **L4 udalosti** — {len(ev)} v horizonte 3 dní"
               + (": " + ", ".join(f"T{e['tier']} {e['ts'][11:16]} {e['title'][:28]}"
                                   for e in ev[:4]) if ev else " (žiadne)"))
    news = c["L5_news"]
    out.append(f"- **L5 správy** — " + ("; ".join(
        f"{n.get('label','?')}: {n.get('text','')[:60]}" for n in news[:5])
        if news else "nenapojené"))

    out += ["", "## Scenáre", "",
            "| | Smer | Typ | Vstup | SL | TP1 (RR) | TP2 (RR) | Objem |",
            "|---|---|---|---|---|---|---|---|"]
    for sc in plan["scenarios"]:
        if sc["side"] is None:
            out.append(f"| **{sc['tag']}** | — | {sc['kind']} | "
                       f"{sc['trigger']} | — | — | — | — |")
            continue
        out.append(
            f"| **{sc['tag']}** | {sc['side']} | {sc['kind']} | "
            f"{sc['entry_lo']:.5f}–{sc['entry_hi']:.5f} | {sc['sl']:.5f} | "
            + (f"{sc['tp1']:.5f} ({sc['rr1']:.1f}) " if sc['tp1'] else "— ")
            + "| "
            + (f"{sc['tp2']:.5f} ({sc['rr2']:.1f}) " if sc['tp2'] else "— ")
            + "| "
            + (f"{sc['volume']:,.0f} " if sc['volume'] else "— ")
            + "|")

    for sc in plan["scenarios"]:
        if sc["invalidation"]:
            out += ["", f"**{sc['tag']} — invalidácia / podmienky**"]
            out += [f"- {x}" for x in sc["invalidation"]]
        if sc["note"]:
            out.append(f"  \n  _{sc['note']}_")

    out += ["", "## Risk checklist", "",
            "- [ ] risk na obchod 0,5 % equity, páka do 5×",
            "- [ ] max 1 otvorená pozícia + 1 čakajúci order, 1 vstup denne",
            "- [ ] denný stop −1,5 %, max DD 15 % od HWM",
            "- [ ] time stop 3 obchodné dni, piatok flat do 19:00 UTC",
            "- [ ] P a A1 sú OCO — aktivácia jedného ruší druhý",
            "- [ ] plán sa počas dňa NEMENÍ (výnimka: news guard)",
            ""]
    return "\n".join(out)


def write(plan: dict, outdir: Path) -> tuple[Path, Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    j = outdir / f"{plan['plan_date']}.json"
    m = outdir / f"{plan['plan_date']}.md"
    for p in (j, m):
        if p.exists():
            raise FileExistsError(
                f"{p} už existuje — plán je nemenný (§5). Ak ho naozaj treba "
                f"pregenerovať, zmaž súbor ručne a zaznamenaj to do denníka.")
    j.write_text(json.dumps(plan, indent=1, ensure_ascii=False))
    m.write_text(to_md(plan))
    return j, m
