"use client";

import { useEffect, useMemo, useState } from "react";
import Competition, {
  accountsOf, type PosRow, type Snap, type StateRow, type TradeRow,
} from "@/components/Competition";
import { createClient } from "@/lib/supabase";
import { useLive } from "@/lib/useLive";

/** Dáta pre porovnanie; zobrazenie je v components/Competition.tsx. */

const ENVS = "env.eq.live,env.like.paper*";   // * = % v PostgREST

async function loadSnaps(envs: string[], start: string): Promise<Snap[]> {
  const sb = createClient();
  const out: Snap[] = [];
  for (const env of envs) {
    // PostgREST vracia najviac 1 000 riadkov naraz; snapshot je každých 5 min.
    for (let from = 0; from < 100_000; from += 1000) {
      const { data, error } = await sb.from("equity_snapshots")
        .select("ts,equity").eq("env", env).gte("ts", start)
        .order("ts", { ascending: true }).range(from, from + 999);
      if (error || !data) break;
      out.push(...data.map((d) => ({ env, ts: d.ts as string, equity: Number(d.equity) })));
      if (data.length < 1000) break;
    }
  }
  return out;
}

export default function VsPage() {
  const { rows: states } = useLive<StateRow>("bot_state", (q) => q.select("*").or(ENVS));
  const accounts = useMemo(() => accountsOf(states), [states]);
  // Predvolené okno = štart najmladšieho virtuálneho účtu (všetci rovnaké podmienky).
  const latest = accounts.reduce<string | null>(
    (m, a) => (a.start && (!m || a.start > m) ? a.start : m), null);
  const [picked, setPicked] = useState<string | null>(null);
  const start = picked ?? latest;

  const { rows: trades } = useLive<TradeRow>("trades", (q) =>
    q.select("env,pnl_usd,closed_at").or(ENVS)
      .gte("closed_at", start ?? "2999-01-01").limit(10_000), [start]);
  const { rows: positions } = useLive<PosRow>("positions", (q) => q.select("*").or(ENVS));

  const envKey = accounts.map((a) => a.env).join(",");
  const [snaps, setSnaps] = useState<Snap[]>([]);
  useEffect(() => {
    if (!start || !envKey) return;
    let alive = true;
    const run = () => loadSnaps(envKey.split(","), start).then((s) => { if (alive) setSnaps(s); });
    run();
    const id = setInterval(run, 5 * 60_000);
    return () => { alive = false; clearInterval(id); };
  }, [start, envKey]);

  return <Competition accounts={accounts} states={states} trades={trades}
                      positions={positions} snaps={snaps} start={start} onStart={setPicked} />;
}
