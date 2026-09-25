"use client";

import DailyPnlTable from "@/components/DailyPnlTable";
import PositionList from "@/components/PositionList";
import { DataStamp, RefreshButton, Section } from "@/components/ui";
import { useLive } from "@/lib/useLive";
import { useEnv } from "@/lib/env";
import { money, pnlClass, signed } from "@/lib/format";
import type { DailyCycle, Position, Trade } from "@/lib/types";

export default function PositionsPage() {
  const { env } = useEnv();
  const { rows: positions, reload } = useLive<Position>("positions", (q) =>
    q.select("*").eq("env", env).order("id", { ascending: true }),
    [env],
  );
  const { rows: daily, reload: reloadDaily } = useLive<DailyCycle>("daily_cycles", (q) =>
    q.select("*").eq("env", env).order("day", { ascending: false }).limit(400),
    [env],
  );
  const { rows: trades, reload: reloadTrades } = useLive<Trade>("trades", (q) =>
    q.select("*").eq("env", env).order("closed_at", { ascending: false }).limit(500),
    [env],
  );
  const refreshAll = () => { reload(); reloadDaily(); reloadTrades(); };

  const newest = positions.reduce<string | null>(
    (a, p) => (!a || (p.updated_at && p.updated_at > a) ? p.updated_at : a),
    null,
  );

  const floating = positions.reduce((a, p) => a + (Number(p.pnl_float) || 0), 0);
  const funding = positions.reduce((a, p) => a + (Number(p.funding_usd) || 0), 0);
  const volume = positions.reduce((a, p) => a + (Number(p.qty) || 0), 0);

  return (
    <main>
      <Section>
        <div className="mb-2 flex items-center gap-2">
          <DataStamp iso={newest} />
          <span className="ml-auto"><RefreshButton onClick={refreshAll} /></span>
        </div>
        <div className="grid grid-cols-3 gap-2">
          <div className="tile">
            <div className="tile-label">Floating</div>
            <div className={`tile-value ${pnlClass(floating)}`}>{signed(floating)}</div>
          </div>
          <div className="tile">
            <div className="tile-label">Swap (držanie)</div>
            <div className={`tile-value ${pnlClass(funding)}`}>{signed(funding)}</div>
          </div>
          <div className="tile">
            <div className="tile-label">Objem</div>
            <div className="tile-value">{money(volume, 0)}</div>
          </div>
        </div>
      </Section>

      <Section
        title="Otvorené"
        meta={`${positions.length}`}
        info="Klepnutím na riadok sa rozbalí objem, TP, swap a tlačidlo na zatvorenie. Swap je naakumulovaný náklad za držanie pozície cez noc — účtuje sa pri dennom rollovere, v stredu trojnásobne; hodnota ide priamo z brokera. Zatvorenie ide cez frontu príkazov — bot ho vyzdvihne do 5 sekúnd, dashboard k brokerovi prístup nemá."
      >
        <PositionList positions={positions} />
      </Section>

      <Section
        title="Denný P/L"
        info="Len dni so zavretým obchodom, podľa UTC dátumu zavretia. Riadok dňa sa dá rozkliknúť na jednotlivé zrealizované obchody; kumulatív beží od najstaršieho zobrazeného dňa."
      >
        <DailyPnlTable daily={daily} trades={trades} />
      </Section>
    </main>
  );
}
