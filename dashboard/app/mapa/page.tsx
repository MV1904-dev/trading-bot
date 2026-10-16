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
        title="Mapa rizika"
        info="Hore pás pozícií: cena zľava doprava, longy nad osou, shorty pod ňou, vybledlé čiarky sú longy, ktoré by grid otvoril cestou dole, kým sa nenaplní kapacita. Sivý podklad je pásmo — jeho dolná hrana vypína len shorty. Ťahaj po páse a pod ním sa ukážu hodnoty pre daný kurz. Pod pásom scenár pádu po 50 pipoch: koľko pozícií bude otvorených, aká bude úroveň marže a koľko treba doliať, aby neklesla pod 100 %. Model jednosmerného pádu bez budúcich swapov: ak kurz osciluje, longy vyberú TP a skutočnosť bude lepšia."
      >
        <RiskMap state={states[0] ?? null} positions={positions} />
      </Section>
    </main>
  );
}
