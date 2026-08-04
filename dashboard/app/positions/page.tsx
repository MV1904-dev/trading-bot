"use client";

import PositionList from "@/components/PositionList";
import { Section } from "@/components/ui";
import { useLive } from "@/lib/useLive";
import { money, pnlClass, signed } from "@/lib/format";
import type { Position } from "@/lib/types";

export default function PositionsPage() {
  const { rows: positions } = useLive<Position>("positions", (q) =>
    q.select("*").order("id", { ascending: true }),
  );

  const floating = positions.reduce((a, p) => a + (Number(p.pnl_float) || 0), 0);
  const funding = positions.reduce((a, p) => a + (Number(p.funding_usd) || 0), 0);
  const volume = positions.reduce((a, p) => a + (Number(p.qty) || 0), 0);

  return (
    <main>
      <Section>
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
    </main>
  );
}
