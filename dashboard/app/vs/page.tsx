"use client";

import { useMemo, useState } from "react";
import Competition, {
  accountsOf, type PosRow, type StateRow, type TradeRow,
} from "@/components/Competition";
import { useLive } from "@/lib/useLive";

/** Dáta pre porovnanie; zobrazenie je v components/Competition.tsx. */

const ENVS = "env.like.paper*";   // * = % v PostgREST; live v súťaži nie je

export default function VsPage() {
  const { rows: states } = useLive<StateRow>("bot_state", (q) => q.select("*").or(ENVS));
  const accounts = useMemo(() => accountsOf(states), [states]);
  // Predvolené okno = štart najmladšieho virtuálneho účtu (všetci rovnaké podmienky).
  const latest = accounts.reduce<string | null>(
    (m, a) => (a.start && (!m || a.start > m) ? a.start : m), null);
  const [picked, setPicked] = useState<string | null>(null);
  const start = picked ?? latest;

  // Kohorta: obchody OTVORENÉ v okne (zavreté sú potom nutne tiež v ňom).
  const { rows: trades } = useLive<TradeRow>("trades", (q) =>
    q.select("env,pnl_usd,opened_at,closed_at").or(ENVS)
      .gte("opened_at", start ?? "2999-01-01").limit(10_000), [start]);
  const { rows: positions } = useLive<PosRow>("positions", (q) => q.select("*").or(ENVS));

  return <Competition accounts={accounts} trades={trades} positions={positions}
                      start={start} onStart={setPicked} />;
}
