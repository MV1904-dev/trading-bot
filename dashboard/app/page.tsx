"use client";

import GridBand from "@/components/GridBand";
import Hero from "@/components/Hero";
import PositionList from "@/components/PositionList";
import { Section } from "@/components/ui";
import { useLive } from "@/lib/useLive";
import { useEnv } from "@/lib/env";
import type { BotState, CalendarEvent, DailyCycle, Position } from "@/lib/types";

export default function Overview() {
  const { env } = useEnv();
  const { rows: states, reload: r1 } = useLive<BotState>("bot_state", (q) =>
    q.select("*").eq("env", env).limit(1),
    [env],
  );
  const { rows: positions, reload: r2 } = useLive<Position>("positions", (q) =>
    q.select("*").eq("env", env).order("opened_at", { ascending: false }),
    [env],
  );
  const { rows: daily, reload: r3 } = useLive<DailyCycle>("daily_cycles", (q) =>
    q.select("*").eq("env", env).order("day", { ascending: false }).limit(400),
    [env],
  );
  // Kalendár je spoločný pre demo aj live — blackout platí rovnako.
  const { rows: events, reload: r4 } = useLive<CalendarEvent>("calendar_events", (q) =>
    q.select("*").order("ts", { ascending: true }).limit(500),
  );
  const refreshAll = () => { r1(); r2(); r3(); r4(); };

  const state = states[0] ?? null;

  return (
    <main>
      <Hero state={state} positions={positions} daily={daily} events={events}
            onRefresh={refreshAll} />

      <Section
        title="Pásmo a najbližšie vstupy"
        info="Kam musí dôjsť kurz pre najbližší vstup. Spúšť sa počíta od kotvy, ktorá už môže byť prekročená — vtedy vstup padne na najbližšom bare. Slabé značky sú ďalšie úrovne mriežky: po vstupe sa kotva presunie na cenu vstupu, takže ďalšia úroveň je o krok ďalej. Vybledená strana neotvára — buď je za hranou pásma, alebo ju vyplo drahé držanie."
      >
        <GridBand state={state} />
      </Section>

      <Section
        title="Pozície"
        meta={positions.length ? `${positions.length} otvorených` : undefined}
        info="Vek nad 3 dni je žltý, nad 7 dní červený — grid má cykly v hodinách, čo visí dni, drží kapitál a platí swap. Klepnutím na riadok sa rozbalí TP, swap za držanie a zatvorenie."
      >
        <PositionList positions={positions} />
      </Section>
    </main>
  );
}
