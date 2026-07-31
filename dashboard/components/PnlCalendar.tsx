"use client";

import { signed } from "@/lib/format";
import type { DailyCycle } from "@/lib/types";

const DAYS = ["Po", "Ut", "St", "Št", "Pi", "So", "Ne"];

/**
 * Denný P/L ako kalendárová mriežka za posledných 8 týždňov. Sila je
 * v tom, že vidno zhluky — grid má typicky série dní v pluse a potom
 * dlhé visenie pod vodou.
 */
export default function PnlCalendar({ daily }: { daily: DailyCycle[] }) {
  const byDay = new Map<string, { pnl: number; cycles: number }>();
  for (const d of daily) {
    const cur = byDay.get(d.day) ?? { pnl: 0, cycles: 0 };
    cur.pnl += Number(d.pnl_usd) || 0;
    cur.cycles += d.cycles || 0;
    byDay.set(d.day, cur);
  }

  const today = new Date();
  // Pondelok tohto týždňa (getDay: nedeľa = 0)
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  monday.setHours(0, 0, 0, 0);

  const weeks: { key: string; pnl: number | null; cycles: number }[][] = [];
  for (let w = 7; w >= 0; w--) {
    const row = [];
    for (let d = 0; d < 7; d++) {
      const day = new Date(monday);
      day.setDate(monday.getDate() - w * 7 + d);
      const key = day.toISOString().slice(0, 10);
      const hit = byDay.get(key);
      row.push({
        key,
        pnl: day > today ? null : (hit?.pnl ?? null),
        cycles: hit?.cycles ?? 0,
      });
    }
    weeks.push(row);
  }

  const max = Math.max(
    1,
    ...[...byDay.values()].map((v) => Math.abs(v.pnl)),
  );

  return (
    <div className="card">
      <span className="card-title">Denný P/L</span>
      <div className="mt-3 flex gap-1">
        <div className="flex flex-col gap-1 pr-1">
          {DAYS.map((d) => (
            <div key={d} className="h-5 text-[10px] leading-5 text-zinc-600">
              {d}
            </div>
          ))}
        </div>
        <div className="flex flex-1 gap-1 overflow-x-auto">
          {weeks.map((week, i) => (
            <div key={i} className="flex flex-col gap-1">
              {week.map((cell) => {
                const v = cell.pnl;
                const intensity =
                  v == null || v === 0 ? 0 : Math.min(Math.abs(v) / max, 1);
                const bg =
                  v == null
                    ? "transparent"
                    : v === 0
                      ? "#27272a"
                      : v > 0
                        ? `rgba(52,211,153,${0.15 + intensity * 0.75})`
                        : `rgba(244,63,94,${0.15 + intensity * 0.75})`;
                return (
                  <div
                    key={cell.key}
                    title={
                      v == null
                        ? cell.key
                        : `${cell.key}: ${signed(v)} (${cell.cycles} cyklov)`
                    }
                    className="h-5 w-5 shrink-0 rounded-sm border border-zinc-900"
                    style={{ background: bg }}
                  />
                );
              })}
            </div>
          ))}
        </div>
      </div>
      <p className="mt-3 text-xs text-zinc-600">
        Posledných 8 týždňov. Sýtosť zodpovedá veľkosti P/L, prázdna bunka
        znamená deň bez zavretého obchodu.
      </p>
    </div>
  );
}
