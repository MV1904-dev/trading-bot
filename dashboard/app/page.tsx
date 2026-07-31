"use client";

import EquityChart from "@/components/EquityChart";
import Hero from "@/components/Hero";
import PnlCalendar from "@/components/PnlCalendar";
import PositionList from "@/components/PositionList";
import DailyPnlTable from "@/components/DailyPnlTable";
import { Section } from "@/components/ui";
import { useLive } from "@/lib/useLive";
import { money, signed } from "@/lib/format";
import type { BotState, DailyCycle, EquityPoint, Position } from "@/lib/types";

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

  const state = states[0] ?? null;
  const swapRatio =
    state?.swap_long != null && state?.swap_short
      ? Math.abs(state.swap_long / state.swap_short)
      : null;

  return (
    <main>
      <Hero state={state} positions={positions} daily={daily} />

      <Section
        title="Pozície"
        meta={positions.length ? `${positions.length} otvorených` : undefined}
        info="Vek nad 3 dni je žltý, nad 7 dní červený — grid má cykly v hodinách, čo visí dni, drží kapitál a platí swap. Klepnutím na riadok sa rozbalí TP, funding a zatvorenie."
      >
        <PositionList positions={positions} limit={4} />
      </Section>

      <Section
        title="Kapitál"
        info="Marža pod 200 % je žltá, pod 100 % červená; broker začne zatvárať pozície sám pri 50 %. Swap sadzby sú v jednotkách brokera — vypovedajúci je ich pomer, nie absolútna hodnota."
      >
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="tile">
            <div className="tile-label">Voľná marža</div>
            <div className="tile-value">{money(state?.free_margin)}</div>
          </div>
          <div className="tile">
            <div className="tile-label">Využitá</div>
            <div className="tile-value">{money(state?.used_margin)}</div>
          </div>
          <div className="tile">
            <div className="tile-label">Swap dnes</div>
            <div className="tile-value">
              {signed(positions.reduce((a, p) => a + (Number(p.funding_usd) || 0), 0))}
            </div>
          </div>
          <div className="tile">
            <div className="tile-label">Long vs short swap</div>
            <div className="tile-value">
              {swapRatio == null ? "—" : `${swapRatio.toFixed(0)}×`}
            </div>
          </div>
        </div>
      </Section>

      <Section title="Equity">
        <EquityChart points={equity} />
      </Section>

      <Section
        title="Denný P/L"
        info="Sýtosť bunky zodpovedá veľkosti P/L, prázdna bunka je deň bez zavretého obchodu. Pod mriežkou sú tie isté dni číselne, vrátane kumulatívu."
      >
        <PnlCalendar daily={daily} />
        <div className="mt-3">
          <DailyPnlTable daily={daily} />
        </div>
      </Section>
    </main>
  );
}
