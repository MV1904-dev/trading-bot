"use client";

import Alerts from "@/components/Alerts";
import BotStatus from "@/components/BotStatus";
import DailyPnlTable from "@/components/DailyPnlTable";
import EquityChart from "@/components/EquityChart";
import Metrics from "@/components/Metrics";
import PnlCalendar from "@/components/PnlCalendar";
import PriceBands from "@/components/PriceBands";
import Roi from "@/components/Roi";
import { useLive } from "@/lib/useLive";
import type {
  BotState, DailyCycle, EquityPoint, Position, Trade,
} from "@/lib/types";

export default function Overview() {
  const { rows: states } = useLive<BotState>("bot_state", (q) =>
    q.select("*").limit(1),
  );
  const { rows: positions } = useLive<Position>("positions", (q) =>
    q.select("*").order("opened_at", { ascending: false }),
  );
  const { rows: equity } = useLive<EquityPoint>("equity_snapshots", (q) =>
    q.select("*").order("ts", { ascending: true }).limit(2000),
  );
  const { rows: daily } = useLive<DailyCycle>("daily_cycles", (q) =>
    q.select("*").order("day", { ascending: false }).limit(400),
  );
  const { rows: firstTrades } = useLive<Trade>("trades", (q) =>
    q.select("closed_at").order("closed_at", { ascending: true }).limit(1),
  );

  const state = states[0] ?? null;

  return (
    <main className="space-y-3">
      <h1 className="px-1 text-lg font-semibold">Prehľad</h1>
      <BotStatus state={state} />
      <PriceBands state={state} />
      <Metrics state={state} positions={positions} daily={daily} />
      <Roi state={state} firstTradeAt={firstTrades[0]?.closed_at ?? null} />
      <Alerts state={state} positions={positions} />
      <EquityChart points={equity} />
      <PnlCalendar daily={daily} />
      <DailyPnlTable daily={daily} />
    </main>
  );
}
