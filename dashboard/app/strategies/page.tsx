"use client";

import CostSplit, {
  addTrade,
  emptySide,
  type SideCosts,
} from "@/components/CostSplit";
import { useLive } from "@/lib/useLive";
import { money, pnlClass, signed } from "@/lib/format";
import type { BotState, DailyCycle, Position, Trade } from "@/lib/types";

type Row = {
  strategy: string;
  cycles: number;
  pnl: number;
  wins: number;
  open: number;
  long: SideCosts;
  short: SideCosts;
};

export default function StrategiesPage() {
  const { rows: states } = useLive<BotState>("bot_state", (q) =>
    q.select("*").limit(1),
  );
  const { rows: trades } = useLive<Trade>("trades", (q) =>
    q.select("*").limit(5000),
  );
  const { rows: positions } = useLive<Position>("positions", (q) =>
    q.select("*"),
  );
  const { rows: daily } = useLive<DailyCycle>("daily_cycles", (q) =>
    q.select("*").limit(400),
  );

  const state = states[0] ?? null;
  const flags = ((state?.config as { strategies?: string[] } | null)
    ?.strategies ?? []) as string[];

  const map = new Map<string, Row>();
  const get = (s: string) => {
    if (!map.has(s))
      map.set(s, {
        strategy: s,
        cycles: 0,
        pnl: 0,
        wins: 0,
        open: 0,
        long: emptySide(),
        short: emptySide(),
      });
    return map.get(s)!;
  };

  for (const t of trades) {
    const r = get(t.strategy);
    const pnl = Number(t.pnl_usd) || 0;
    r.cycles += 1;
    r.pnl += pnl;
    if (pnl > 0) r.wins += 1;
    addTrade(t.side === "long" ? r.long : r.short, t);
  }
  for (const p of positions) get(p.strategy).open += 1;
  for (const d of daily) get(d.strategy);

  const rows = [...map.values()].sort((a, b) => b.cycles - a.cycles);

  if (rows.length === 0) {
    return (
      <main className="space-y-3">
        <h1 className="px-1 text-lg font-semibold">Výkon per stratégia</h1>
        <p className="card text-sm text-muted">Zatiaľ žiadne dáta.</p>
      </main>
    );
  }

  return (
    <main className="space-y-3">
      <h1 className="px-1 text-lg font-semibold">Výkon per stratégia</h1>

      {rows.map((r) => {
        const flag = flags.find((f) => f.startsWith(r.strategy));
        const on = flag?.includes(": ON");
        const winRate = r.cycles ? (r.wins / r.cycles) * 100 : 0;
        const commission = r.long.commission + r.short.commission;
        const spread = r.long.spread + r.short.spread;
        const funding = r.long.funding + r.short.funding;
        const costs = commission + spread - Math.min(funding, 0);
        const gross = r.pnl + costs;
        const costRatio = gross > 0 ? (costs / gross) * 100 : null;
        const spreadKnown = r.long.spreadKnown + r.short.spreadKnown;

        return (
          <div key={r.strategy} className="card space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-semibold">{r.strategy}</h2>
              {flag && (
                <span
                  className={`rounded-full px-2 py-0.5 text-xs ${
                    on
                      ? "bg-emerald-600/15 text-pos"
                      : "bg-line text-muted"
                  }`}
                >
                  {on ? "ON" : "OFF"}
                </span>
              )}
              {r.open > 0 && (
                <span className="text-xs text-faint">{r.open} otvorených</span>
              )}
              <span className={`ml-auto font-mono text-lg ${pnlClass(r.pnl)}`}>
                {signed(r.pnl)}
              </span>
            </div>

            <div className="grid grid-cols-3 gap-3 text-sm">
              <div>
                <div className="card-title">Cykly</div>
                <div className="mt-0.5 font-mono">{r.cycles}</div>
              </div>
              <div>
                <div className="card-title">Win rate</div>
                <div className="mt-0.5 font-mono">
                  {r.cycles ? `${winRate.toFixed(0)} %` : "—"}
                </div>
              </div>
              <div>
                <div className="card-title">Náklady / hrubý</div>
                <div className="mt-0.5 font-mono">
                  {costRatio == null ? "—" : `${costRatio.toFixed(1)} %`}
                </div>
              </div>
            </div>

            <div>
              <div className="card-title mb-2">Rozpad nákladov spolu</div>
              <CostBar
                commission={commission}
                spread={spread}
                funding={funding}
              />
              {spreadKnown < r.cycles && (
                <p className="mt-2 text-xs text-faint">
                  Spread je známy pre {spreadKnown} z {r.cycles} obchodov — bot
                  ho zaznamenáva až od 31. 7. 2026, staršie sa doň
                  nezapočítavajú.
                </p>
              )}
            </div>

            <CostSplit long={r.long} short={r.short} />
          </div>
        );
      })}
    </main>
  );
}

function CostBar({
  commission,
  spread,
  funding,
}: {
  commission: number;
  spread: number;
  funding: number;
}) {
  // Funding môže byť príjem aj náklad; do pruhu berieme jeho absolútnu
  // veľkosť a znamienko ukazujeme v legende.
  const items = [
    { label: "Provízie", v: commission, cls: "bg-sky-500" },
    { label: "Spread", v: spread, cls: "bg-violet-500" },
    { label: "Funding", v: Math.abs(funding), cls: "bg-amber-500" },
  ];
  const total = items.reduce((a, i) => a + i.v, 0);

  return (
    <>
      <div className="flex h-2 overflow-hidden rounded-full bg-line">
        {total > 0 &&
          items.map((i) => (
            <div
              key={i.label}
              className={i.cls}
              style={{ width: `${(i.v / total) * 100}%` }}
            />
          ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {items.map((i) => (
          <span key={i.label} className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${i.cls}`} />
            <span className="text-muted">{i.label}</span>
            <span className="font-mono">
              {i.label === "Funding" ? signed(funding) : money(i.v)}
            </span>
          </span>
        ))}
      </div>
    </>
  );
}
