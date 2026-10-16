"use client";

import { useEffect, useState } from "react";
import Competition, { type Env, type PosRow, type Snap, type TradeRow } from "@/components/Competition";
import { createClient } from "@/lib/supabase";
import { useLive } from "@/lib/useLive";
import type { BotState } from "@/lib/types";

/** Dáta pre porovnanie; zobrazenie je v components/Competition.tsx. */

async function loadSnaps(start: string): Promise<Snap[]> {
  const sb = createClient();
  const out: Snap[] = [];
  for (const env of ["live", "paper"] as Env[]) {
    // PostgREST vracia najviac 1 000 riadkov naraz; snapshot je každých 5 min.
    for (let from = 0; from < 50_000; from += 1000) {
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
  const { rows: states } = useLive<BotState & { env: Env }>("bot_state", (q) =>
    q.select("*").in("env", ["live", "paper"]));
  const paper = states.find((s) => s.env === "paper") ?? null;
  const live = states.find((s) => s.env === "live") ?? null;
  const start = (paper?.config?.started_at as string | undefined) ?? null;

  const { rows: trades } = useLive<TradeRow>("trades", (q) =>
    q.select("env,pnl_usd,closed_at").in("env", ["live", "paper"])
      .gte("closed_at", start ?? "2999-01-01").limit(5000), [start]);
  const { rows: positions } = useLive<PosRow>("positions", (q) =>
    q.select("*").in("env", ["live", "paper"]));

  const [snaps, setSnaps] = useState<Snap[]>([]);
  useEffect(() => {
    if (!start) return;
    let alive = true;
    const run = () => loadSnaps(start).then((s) => { if (alive) setSnaps(s); });
    run();
    const id = setInterval(run, 5 * 60_000);
    return () => { alive = false; clearInterval(id); };
  }, [start]);

  return <Competition live={live} paper={paper} trades={trades} positions={positions} snaps={snaps} />;
}
