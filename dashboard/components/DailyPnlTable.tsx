"use client";

import { money, pnlClass, signed } from "@/lib/format";
import { Empty } from "@/components/ui";
import type { DailyCycle } from "@/lib/types";

/**
 * Denný P/L za 30 dní ako riadky, nie tabuľka — tabuľka mala pevnú
 * minimálnu šírku a na telefóne sa posúvala do bokov.
 *
 * Dni bez zavretého obchodu sa nevypisujú: pri gridoch je ich väčšina
 * a riadok s nulou nič nehovorí. Kumulatív preto beží cez obchodované dni.
 */
export default function DailyPnlTable({ daily }: { daily: DailyCycle[] }) {
  const cutoff = Date.now() - 30 * 86_400_000;

  const byDay = new Map<string, { pnl: number; cycles: number }>();
  for (const d of daily) {
    if (new Date(d.day).getTime() < cutoff) continue;
    const cur = byDay.get(d.day) ?? { pnl: 0, cycles: 0 };
    cur.pnl += Number(d.pnl_usd) || 0;
    cur.cycles += d.cycles || 0;
    byDay.set(d.day, cur);
  }

  const asc = [...byDay.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  let run = 0;
  const rows = asc.map(([day, v]) => {
    run += v.pnl;
    return { day, ...v, cum: run };
  });
  const view = [...rows].reverse();

  if (view.length === 0) return <Empty>Zatiaľ žiadny zavretý obchod.</Empty>;

  const wins = rows.filter((r) => r.pnl > 0).length;
  const cycles = rows.reduce((a, r) => a + r.cycles, 0);

  return (
    <div>
      <p className="mb-1 text-xs text-faint">
        {rows.length} dní · {wins} v pluse · {cycles} cyklov ·{" "}
        <span className={`num ${pnlClass(run)}`}>{signed(run)}</span>
      </p>

      {view.map((r) => (
        <div key={r.day} className="row">
          <span className="w-20 shrink-0 text-xs text-muted">
            {new Date(r.day).toLocaleDateString("sk-SK", {
              weekday: "short",
              day: "2-digit",
              month: "2-digit",
            })}
          </span>
          <span className="num text-xs text-faint">{r.cycles}×</span>
          <span className={`num ml-auto ${pnlClass(r.pnl)}`}>{signed(r.pnl)}</span>
          <span className="num w-16 shrink-0 text-right text-xs text-faint">
            {money(r.cum)}
          </span>
        </div>
      ))}
    </div>
  );
}
