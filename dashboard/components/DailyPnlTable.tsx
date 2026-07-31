"use client";

import { money, pnlClass, signed } from "@/lib/format";
import type { DailyCycle } from "@/lib/types";

/**
 * Číselný prehľad denného P/L za 30 dní. Kalendár nad tým ukazuje tvar
 * (zhluky plusových a mínusových dní), táto tabuľka konkrétne čísla
 * vrátane kumulatívu — z toho je vidieť, či séria dobrých dní naozaj
 * dobehla predošlý prepad.
 *
 * Dni bez zavretého obchodu sa nevypisujú: pri gridoch je ich veľa a
 * riadok s nulou nič nehovorí. Kumulatív preto beží cez obchodované dni.
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

  // Kumulatív sa počíta od najstaršieho dňa, zobrazuje sa od najnovšieho.
  const asc = [...byDay.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  let run = 0;
  const rows = asc.map(([day, v]) => {
    run += v.pnl;
    return { day, ...v, cum: run };
  });
  const view = [...rows].reverse();

  if (view.length === 0) {
    return (
      <div className="card">
        <span className="card-title">Denný P/L — 30 dní</span>
        <p className="mt-3 text-sm text-muted">
          Zatiaľ žiadny zavretý obchod v tomto období.
        </p>
      </div>
    );
  }

  const total = run;
  const cycles = rows.reduce((a, r) => a + r.cycles, 0);
  const wins = rows.filter((r) => r.pnl > 0).length;

  return (
    <div className="card">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="card-title">Denný P/L — 30 dní</span>
        <span className="text-xs text-faint">
          {rows.length} obchodovaných dní · {wins} v pluse · {cycles} cyklov ·{" "}
          <span className={`font-mono ${pnlClass(total)}`}>{signed(total)}</span>
        </span>
      </div>

      <div className="table-wrap mt-3">
        <table className="data !min-w-[420px]">
          <thead>
            <tr>
              <th>Dátum</th>
              <th className="text-right">Obchodov</th>
              <th className="text-right">Čistý P/L</th>
              <th className="text-right">Kumulatívne</th>
            </tr>
          </thead>
          <tbody>
            {view.map((r) => (
              <tr key={r.day}>
                <td>
                  {new Date(r.day).toLocaleDateString("sk-SK", {
                    weekday: "short",
                    day: "2-digit",
                    month: "2-digit",
                  })}
                </td>
                <td className="text-right font-mono text-muted">{r.cycles}</td>
                <td className={`text-right font-mono ${pnlClass(r.pnl)}`}>
                  {signed(r.pnl)}
                </td>
                <td className={`text-right font-mono ${pnlClass(r.cum)}`}>
                  {money(r.cum)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
