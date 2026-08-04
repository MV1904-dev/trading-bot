"use client";

import { heldFor, money, pnlClass, price, signed } from "@/lib/format";
import { Empty, ExpandableRow } from "@/components/ui";
import type { DailyCycle, Trade } from "@/lib/types";

/**
 * Denný P/L za 30 dní ako rozklikávacie riadky — nahrádza equity graf.
 * Ten cez víkend interpoloval medzi snapshotmi a ukazoval pohyb, ktorý
 * sa nikdy nestal; tabuľka stojí na zavretých obchodoch, tam sa klamať
 * nedá. Rozklik dňa ukáže jednotlivé zrealizované obchody.
 */
export default function DailyPnlTable({
  daily,
  trades,
}: {
  daily: DailyCycle[];
  trades: Trade[];
}) {
  const cutoff = Date.now() - 30 * 86_400_000;

  const byDay = new Map<string, { pnl: number; cycles: number }>();
  for (const d of daily) {
    if (new Date(d.day).getTime() < cutoff) continue;
    const cur = byDay.get(d.day) ?? { pnl: 0, cycles: 0 };
    cur.pnl += Number(d.pnl_usd) || 0;
    cur.cycles += d.cycles || 0;
    byDay.set(d.day, cur);
  }

  const tradesByDay = new Map<string, Trade[]>();
  for (const t of trades) {
    if (!t.closed_at) continue;
    const day = t.closed_at.slice(0, 10);
    if (!tradesByDay.has(day)) tradesByDay.set(day, []);
    tradesByDay.get(day)!.push(t);
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
        {rows.length} dní · {wins} v pluse · {cycles} obchodov ·{" "}
        <span className={`num ${pnlClass(run)}`}>{signed(run)}</span>
      </p>

      {view.map((r) => {
        const dayTrades = (tradesByDay.get(r.day) ?? []).sort((a, b) =>
          (a.closed_at ?? "").localeCompare(b.closed_at ?? ""),
        );
        return (
          <ExpandableRow
            key={r.day}
            summary={
              <>
                <span className="w-20 shrink-0 text-xs text-muted">
                  {new Date(r.day).toLocaleDateString("sk-SK", {
                    weekday: "short",
                    day: "2-digit",
                    month: "2-digit",
                  })}
                </span>
                <span className="num text-xs text-faint">{r.cycles}×</span>
                <span className={`num ml-auto ${pnlClass(r.pnl)}`}>
                  {signed(r.pnl)}
                </span>
                <span className="num w-16 shrink-0 text-right text-xs text-faint">
                  {money(r.cum)}
                </span>
              </>
            }
          >
            {dayTrades.length === 0 ? (
              <p className="py-1 text-xs text-faint">
                Detail obchodov nie je v načítanom okne histórie.
              </p>
            ) : (
              dayTrades.map((t) => (
                <div key={t.id} className="flex items-center gap-2 py-1 text-xs">
                  <span className="num text-faint">
                    {t.closed_at?.slice(11, 16)}
                  </span>
                  <span className={t.side === "long" ? "chip-long" : "chip-short"}>
                    {t.side === "long" ? "L" : "S"}
                  </span>
                  <span className="num">
                    {price(t.entry_price)} → {price(t.close_price)}
                  </span>
                  <span className="text-faint">
                    {heldFor(t.opened_at, t.closed_at)}
                  </span>
                  {t.manual_close && <span className="text-warn">ručne</span>}
                  <span className={`num ml-auto ${pnlClass(t.pnl_usd)}`}>
                    {signed(t.pnl_usd)}
                  </span>
                </div>
              ))
            )}
          </ExpandableRow>
        );
      })}
    </div>
  );
}
