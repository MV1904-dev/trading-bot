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
        info="Mapa ide oboma smermi: pri páde grid pridáva longy, pri raste shorty, a každý smer má vlastný margin call (↓ a ↑) a stop-out. Pás: cena zľava doprava, longy nad osou, shorty pod ňou, vybledlé čiarky sú pozície, ktoré by grid ešte otvoril, kým sa nenaplní kapacita. Sivý podklad je pásmo — dolná hrana vypína len shorty, horná len longy. Ťahaj po páse a pod ním sa ukážu hodnoty pre daný kurz. Scenár pod pásom ide po 50 pipoch hore aj dole: koľko pozícií bude otvorených, aká bude úroveň marže a koľko treba doliať, aby neklesla pod 100 %. Model jednosmerného pohybu bez budúcich swapov: ak kurz osciluje, pozície vyberú TP a skutočnosť bude lepšia."
      >
        <RiskMap state={states[0] ?? null} positions={positions} />
      </Section>
    </main>
  );
}
