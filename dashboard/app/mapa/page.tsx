"use client";

import RiskMap from "@/components/RiskMap";
import { Section } from "@/components/ui";
import { useLive } from "@/lib/useLive";
import { useEnv } from "@/lib/env";
import type { BotState, Position } from "@/lib/types";

export default function MapaPage() {
  const { env } = useEnv();
  const { rows: states } = useLive<BotState>("bot_state", (q) =>
    q.select("*").eq("env", env).limit(1), [env]);
  const { rows: positions } = useLive<Position>("positions", (q) =>
    q.select("*").eq("env", env).order("entry_price", { ascending: false }), [env]);

  return (
    <main>
      <Section
        title="Mapa celej osi"
        info="Celý rozsah 1,00–1,30 naraz. Zelený pás je tam, kde bot otvára longy, modrý tam, kde shorty — a vidno na ňom, že pásmo nie je symetrická ochrana: dolná hrana vypína len shorty, takže pod ňou longy pribúdajú ďalej. Oranžová plocha je suma, ktorú by bolo treba doliať, aby margin level neklesol pod 100 %. Je to model jednosmerného pádu bez swapov: ak kurz po ceste osciluje, longy vyberú TP, marža sa uvoľní a skutočnosť bude lepšia. Ťahaj myšou po grafe (alebo ťukni na mobile) a čítaj hodnoty pre ľubovoľný kurz."
      >
        <RiskMap state={states[0] ?? null} positions={positions} />
      </Section>
    </main>
  );
}
