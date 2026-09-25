"use client";

import { useState } from "react";
import { heldFor, money, pnlClass, price, signed } from "@/lib/format";
import { Empty, ExpandableRow } from "@/components/ui";
import type { DailyCycle, Trade } from "@/lib/types";

/**
 * P/L po dňoch, týždňoch alebo mesiacoch ako rozklikávacie riadky —
 * nahrádza equity graf. Ten cez víkend interpoloval medzi snapshotmi a
 * ukazoval pohyb, ktorý sa nikdy nestal; tabuľka stojí na zavretých
 * obchodoch, tam sa klamať nedá.
 *
 * Rozklik ide o úroveň nižšie: deň ukáže jednotlivé obchody, týždeň a
 * mesiac ukážu dni, z ktorých sa skladajú. Vypísať pri mesiaci všetkých
 * ~200 obchodov by bolo nečitateľné.
 */

type Bucket = "day" | "week" | "month";

const BUCKETS: { id: Bucket; label: string; days: number; unit: string }[] = [
  { id: "day", label: "Dni", days: 30, unit: "dní" },
  { id: "week", label: "Týždne", days: 182, unit: "týždňov" },
  { id: "month", label: "Mesiace", days: 730, unit: "mesiacov" },
];

/** Pondelok týždňa, do ktorého dátum patrí (ISO týždeň, v UTC). */
function mondayOf(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return d.toISOString().slice(0, 10);
}

const dayLabel = (day: string) =>
  new Date(`${day}T00:00:00Z`).toLocaleDateString("sk-SK", {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
    timeZone: "UTC",
  });

function bucketKey(day: string, bucket: Bucket): string {
  if (bucket === "week") return mondayOf(day);
  if (bucket === "month") return day.slice(0, 7);
  return day;
}

function bucketLabel(key: string, bucket: Bucket): string {
  if (bucket === "month") {
    return new Date(`${key}-01T00:00:00Z`).toLocaleDateString("sk-SK", {
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    });
  }
  if (bucket === "week") {
    const end = new Date(`${key}T00:00:00Z`);
    end.setUTCDate(end.getUTCDate() + 6);
    const fmt = (d: Date) =>
      d.toLocaleDateString("sk-SK", {
        day: "2-digit",
        month: "2-digit",
        timeZone: "UTC",
      });
    return `${fmt(new Date(`${key}T00:00:00Z`))} – ${fmt(end)}`;
  }
  return dayLabel(key);
}

export default function DailyPnlTable({
  daily,
  trades,
}: {
  daily: DailyCycle[];
  trades: Trade[];
}) {
  const [bucket, setBucket] = useState<Bucket>("day");
  const spec = BUCKETS.find((b) => b.id === bucket)!;
  const cutoff = Date.now() - spec.days * 86_400_000;

  // dni najprv zosumarizujeme samostatne — týždeň a mesiac sa z nich
  // poskladajú a zároveň ich potrebujeme do rozkliku
  const byDay = new Map<string, { pnl: number; cycles: number }>();
  for (const d of daily) {
    if (new Date(`${d.day}T00:00:00Z`).getTime() < cutoff) continue;
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

  const groups = new Map<
    string,
    { pnl: number; cycles: number; days: string[] }
  >();
  for (const [day, v] of [...byDay.entries()].sort((a, b) =>
    a[0].localeCompare(b[0]),
  )) {
    const key = bucketKey(day, bucket);
    const cur = groups.get(key) ?? { pnl: 0, cycles: 0, days: [] };
    cur.pnl += v.pnl;
    cur.cycles += v.cycles;
    cur.days.push(day);
    groups.set(key, cur);
  }

  let run = 0;
  const rows = [...groups.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([key, v]) => {
      run += v.pnl;
      return { key, ...v, cum: run };
    });

  const wins = rows.filter((r) => r.pnl > 0).length;
  const cycles = rows.reduce((a, r) => a + r.cycles, 0);

  const switcher = (
    <div className="mb-2 flex overflow-hidden rounded-md border border-hair text-[11px] font-semibold">
      {BUCKETS.map((b) => (
        <button
          key={b.id}
          onClick={() => setBucket(b.id)}
          aria-pressed={bucket === b.id}
          className={`px-2.5 py-1 transition-colors ${
            bucket === b.id ? "bg-surface text-ink" : "text-faint hover:text-ink"
          }`}
        >
          {b.label}
        </button>
      ))}
    </div>
  );

  if (rows.length === 0) {
    return (
      <div>
        {switcher}
        <Empty>Zatiaľ žiadny zavretý obchod.</Empty>
      </div>
    );
  }

  return (
    <div>
      {switcher}

      <p className="mb-1 text-xs text-faint">
        {rows.length} {spec.unit} · {wins} v pluse · {cycles} obchodov ·{" "}
        <span className={`num ${pnlClass(run)}`}>{signed(run)}</span>
      </p>

      {[...rows].reverse().map((r) => {
        const dayTrades =
          bucket === "day"
            ? (tradesByDay.get(r.key) ?? []).sort((a, b) =>
                (a.closed_at ?? "").localeCompare(b.closed_at ?? ""),
              )
            : [];
        return (
          <ExpandableRow
            key={r.key}
            summary={
              <>
                <span className="w-28 shrink-0 text-xs text-muted">
                  {bucketLabel(r.key, bucket)}
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
            {bucket !== "day" ? (
              [...r.days].reverse().map((day) => {
                const v = byDay.get(day)!;
                return (
                  <div key={day} className="flex items-center gap-2 py-1 text-xs">
                    <span className="text-muted">{dayLabel(day)}</span>
                    <span className="num text-faint">{v.cycles}×</span>
                    <span className={`num ml-auto ${pnlClass(v.pnl)}`}>
                      {signed(v.pnl)}
                    </span>
                  </div>
                );
              })
            ) : dayTrades.length === 0 ? (
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
