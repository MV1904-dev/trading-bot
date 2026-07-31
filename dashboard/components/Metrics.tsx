"use client";

import { money, pnlClass, signed } from "@/lib/format";
import type { BotState, DailyCycle, Position } from "@/lib/types";

function Tile({
  label,
  value,
  cls = "",
  sub,
}: {
  label: string;
  value: string;
  cls?: string;
  sub?: string;
}) {
  return (
    <div className="card">
      <div className="card-title">{label}</div>
      <div className={`mt-1 font-mono text-xl ${cls}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-faint">{sub}</div>}
    </div>
  );
}

export default function Metrics({
  state,
  positions,
  daily,
}: {
  state: BotState | null;
  positions: Position[];
  daily: DailyCycle[];
}) {
  const today = new Date().toISOString().slice(0, 10);
  const todayRows = daily.filter((d) => d.day === today);
  const todayPnl = todayRows.reduce((a, d) => a + (Number(d.pnl_usd) || 0), 0);
  const todayCycles = todayRows.reduce((a, d) => a + d.cycles, 0);
  const floating = positions.reduce((a, p) => a + (Number(p.pnl_float) || 0), 0);

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile
        label="Účet"
        value={money(state?.equity ?? state?.balance)}
        sub={`realizovaná ${money(state?.balance)}`}
      />
      <Tile
        label="Dnes"
        value={signed(todayPnl)}
        cls={pnlClass(todayPnl)}
        sub={`${todayCycles} ${todayCycles === 1 ? "cyklus" : "cyklov"}`}
      />
      <Tile
        label="Floating"
        value={signed(floating)}
        cls={pnlClass(floating)}
        sub={`${positions.length} ${positions.length === 1 ? "pozícia" : "pozícií"}`}
      />
      <Tile
        label="Cykly / deň"
        value={
          daily.length === 0
            ? "—"
            : (
                daily.reduce((a, d) => a + d.cycles, 0) /
                new Set(daily.map((d) => d.day)).size
              ).toFixed(1)
        }
        sub="priemer za obchodované dni"
      />
    </div>
  );
}
