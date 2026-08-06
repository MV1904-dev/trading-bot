"use client";

import Hero from "@/components/Hero";
import PositionList from "@/components/PositionList";
import DailyPnlTable from "@/components/DailyPnlTable";
import { Section } from "@/components/ui";
import { useLive } from "@/lib/useLive";
import { useEnv } from "@/lib/env";
import { money, signed } from "@/lib/format";
import type { BotState, DailyCycle, Position, Trade } from "@/lib/types";

export default function Overview() {
  const { env } = useEnv();
  const { rows: states } = useLive<BotState>("bot_state", (q) =>
    q.select("*").eq("env", env).limit(1),
    [env],
  );
  const { rows: positions } = useLive<Position>("positions", (q) =>
    q.select("*").eq("env", env).order("opened_at", { ascending: false }),
    [env],
  );
  const { rows: daily } = useLive<DailyCycle>("daily_cycles", (q) =>
    q.select("*").eq("env", env).order("day", { ascending: false }).limit(400),
    [env],
  );
  const { rows: trades } = useLive<Trade>("trades", (q) =>
    q.select("*").eq("env", env).order("closed_at", { ascending: false }).limit(500),
    [env],
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
        info="Vek nad 3 dni je žltý, nad 7 dní červený — grid má cykly v hodinách, čo visí dni, drží kapitál a platí swap. Klepnutím na riadok sa rozbalí TP, swap za držanie a zatvorenie."
      >
        <PositionList positions={positions} limit={4} />
      </Section>

      <Section
        title="Kapitál"
        info="Marža pod 200 % je žltá, pod 100 % červená; broker začne zatvárať pozície sám pri 50 %. Swap otvorených = naakumulovaný náklad za držanie otvorených pozícií cez noc, priamo z brokera."
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
            <div className="tile-label">Swap otvorených</div>
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

      <Section
        title="Denný P/L"
        info="Len dni so zavretým obchodom, podľa UTC dátumu zavretia. Riadok dňa sa dá rozkliknúť na jednotlivé zrealizované obchody; kumulatív beží od najstaršieho zobrazeného dňa."
      >
        <DailyPnlTable daily={daily} trades={trades} />
      </Section>
    </main>
  );
}
