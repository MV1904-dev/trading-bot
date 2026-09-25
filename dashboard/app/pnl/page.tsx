"use client";

import DailyPnlTable from "@/components/DailyPnlTable";
import { DataStamp, RefreshButton, Section } from "@/components/ui";
import { useLive } from "@/lib/useLive";
import { useEnv } from "@/lib/env";
import type { DailyCycle, Trade } from "@/lib/types";

export default function PnlPage() {
  const { env } = useEnv();
  const { rows: daily, reload: reloadDaily } = useLive<DailyCycle>("daily_cycles", (q) =>
    q.select("*").eq("env", env).order("day", { ascending: false }).limit(400),
    [env],
  );
  const { rows: trades, reload: reloadTrades } = useLive<Trade>("trades", (q) =>
    q.select("*").eq("env", env).order("closed_at", { ascending: false }).limit(500),
    [env],
  );
  const refreshAll = () => { reloadDaily(); reloadTrades(); };

  const newest = trades.reduce<string | null>(
    (a, t) => (!a || (t.closed_at && t.closed_at > a) ? t.closed_at : a),
    null,
  );

  return (
    <main>
      <Section
        title="P/L"
        info="Len obdobia so zavretým obchodom, podľa UTC dátumu zavretia. Deň sa dá rozkliknúť na jednotlivé zrealizované obchody, týždeň a mesiac na dni, z ktorých sa skladajú. Kumulatív vpravo beží od najstaršieho zobrazeného obdobia, nie od začiatku účtu — po prepnutí pohľadu sa preto mení. Dni: 30, týždne: pol roka, mesiace: 2 roky."
      >
        <div className="mb-2 flex items-center gap-2">
          <DataStamp iso={newest} />
          <span className="ml-auto"><RefreshButton onClick={refreshAll} /></span>
        </div>
        <DailyPnlTable daily={daily} trades={trades} />
      </Section>
    </main>
  );
}
