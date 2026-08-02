#!/usr/bin/env python3
"""Pošle vygenerovaný plán do Supabase pre dashboard.

Plán ide so statusom 'pending'. Do reality sa dostane až keď ho človek
v dashboarde schváli — executor bez toho nesmie vstúpiť. Opakovaný push
toho istého dňa NEPREPÍŠE už rozhodnutý plán (§5: plán je nemenný).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading.sync_supabase import SupabaseSync


def push(plan: dict) -> bool:
    sb = SupabaseSync()
    if not sb.enabled:
        print("Supabase nie je nastavená (SUPABASE_URL/SERVICE_KEY)", file=sys.stderr)
        return False

    existing = sb._req("GET", f"daily_plans?plan_date=eq.{plan['plan_date']}"
                              f"&select=status")
    if existing and existing[0]["status"] != "pending":
        print(f"plán {plan['plan_date']} je už {existing[0]['status']} "
              f"— neprepisujem")
        return False

    row = {
        "plan_date": plan["plan_date"],
        "generated_at": plan["generated_at"],
        "data_source": plan.get("data_source"),
        "stale_warning": plan.get("WARNING_stale_data"),
        "symbol": plan["symbol"],
        "price_at_build": plan["price_at_build"],
        "atr_d1_pips": plan["atr_d1_pips"],
        "equity": plan["equity"],
        "context": plan["context"],
        "scenarios": plan["scenarios"],
        "status": "pending",
    }
    ok = sb._upsert("daily_plans", [row], on_conflict="plan_date")
    print(f"plán {plan['plan_date']} {'odoslaný' if ok else 'ZLYHAL'}")
    return ok


if __name__ == "__main__":
    push(json.loads(Path(sys.argv[1]).read_text()))
