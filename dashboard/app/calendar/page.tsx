"use client";

import { useLive } from "@/lib/useLive";
import { dateTime, signed } from "@/lib/format";
import type { CalendarEvent, DailyCycle, Trade } from "@/lib/types";

const BLACKOUT_MIN = 30;

/**
 * Kedy sa neobchoduje a kedy sa neobchodovalo.
 *
 * Hore nadchádzajúce blackouty (±30 min okolo high-impact udalosti), dole
 * spätný pohľad na dni bez cyklu — pri gridoch je dôležité vedieť
 * rozlíšiť "nebolo čo obchodovať" od "bot mal zakázané".
 */
export default function CalendarPage() {
  const { rows: events } = useLive<CalendarEvent>("calendar_events", (q) =>
    q.select("*").order("ts", { ascending: true }).limit(500),
  );
  const { rows: daily } = useLive<DailyCycle>("daily_cycles", (q) =>
    q.select("*").order("day", { ascending: false }).limit(120),
  );
  const { rows: trades } = useLive<Trade>("trades", (q) =>
    q.select("closed_at").limit(5000),
  );

  const now = Date.now();
  const upcoming = events.filter((e) => new Date(e.ts).getTime() >= now);
  const past = events
    .filter((e) => new Date(e.ts).getTime() < now)
    .slice(-30)
    .reverse();

  // Dni bez zavretého obchodu za posledných 30 dní, s dôvodom podľa toho,
  // či na ne padla high-impact udalosť alebo víkend.
  const traded = new Set(daily.map((d) => d.day));
  const eventDays = new Set(
    events.map((e) => new Date(e.ts).toISOString().slice(0, 10)),
  );
  const quiet: { day: string; reason: string }[] = [];
  for (let i = 1; i <= 30; i++) {
    const d = new Date(now - i * 86_400_000);
    const key = d.toISOString().slice(0, 10);
    if (traded.has(key)) continue;
    const dow = d.getDay();
    quiet.push({
      day: key,
      reason:
        dow === 0 || dow === 6
          ? "víkend — trh zatvorený"
          : eventDays.has(key)
            ? "high-impact udalosť (blackout)"
            : "bez zavretého obchodu",
    });
  }

  const firstTrade = trades
    .map((t) => t.closed_at)
    .filter(Boolean)
    .sort()[0];

  return (
    <main className="space-y-3">
      <h1 className="px-1 text-lg font-semibold">Kalendár obchodovania</h1>

      <div className="card">
        <span className="card-title">Nadchádzajúce blackouty</span>
        {upcoming.length === 0 ? (
          <p className="mt-3 text-sm text-muted">
            Žiadne high-impact udalosti v načítanom kalendári. Bot ho obnovuje
            priebežne, dopredu vidí spravidla na týždeň.
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {upcoming.slice(0, 12).map((e) => {
              const t = new Date(e.ts).getTime();
              const from = new Date(t - BLACKOUT_MIN * 60_000);
              const to = new Date(t + BLACKOUT_MIN * 60_000);
              const soon = t - now < 6 * 3600_000;
              return (
                <li
                  key={`${e.ts}-${e.title}`}
                  className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line/50 pb-2 text-sm last:border-0"
                >
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs ${
                      e.currency === "USD"
                        ? "bg-short/15 text-short"
                        : "bg-long/15 text-long"
                    }`}
                  >
                    {e.currency}
                  </span>
                  <span className={soon ? "text-warn" : ""}>{e.title}</span>
                  <span className="ml-auto font-mono text-xs text-faint">
                    {from.toLocaleTimeString("sk-SK", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                    –
                    {to.toLocaleTimeString("sk-SK", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                  <span className="font-mono text-xs text-faint">
                    {new Date(e.ts).toLocaleDateString("sk-SK", {
                      weekday: "short",
                      day: "2-digit",
                      month: "2-digit",
                    })}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
        <p className="mt-3 text-xs text-faint">
          Bot nevstupuje ±{BLACKOUT_MIN} minút okolo týchto udalostí. Otvorené
          pozície bežia ďalej, blokujú sa len nové vstupy.
        </p>
      </div>

      <div className="card">
        <span className="card-title">Dni bez zavretého obchodu (30 dní)</span>
        {quiet.length === 0 ? (
          <p className="mt-3 text-sm text-muted">
            Každý deň mal aspoň jeden zavretý obchod.
          </p>
        ) : (
          <div className="table-wrap mt-3">
            <table className="data !min-w-[360px]">
              <thead>
                <tr>
                  <th>Dátum</th>
                  <th>Dôvod</th>
                </tr>
              </thead>
              <tbody>
                {quiet.map((q) => (
                  <tr key={q.day}>
                    <td>
                      {new Date(q.day).toLocaleDateString("sk-SK", {
                        weekday: "short",
                        day: "2-digit",
                        month: "2-digit",
                      })}
                    </td>
                    <td className="text-muted">{q.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {firstTrade && (
          <p className="mt-3 text-xs text-faint">
            Prvý zaznamenaný obchod: {dateTime(firstTrade)}. Dni pred ním sú
            v zozname preto, že bot vtedy ešte nebežal.
          </p>
        )}
      </div>

      {past.length > 0 && (
        <div className="card">
          <span className="card-title">Posledné blackouty</span>
          <ul className="mt-3 space-y-1 text-sm">
            {past.slice(0, 10).map((e) => (
              <li
                key={`${e.ts}-${e.title}`}
                className="flex flex-wrap items-baseline gap-x-3"
              >
                <span className="text-xs text-faint">{e.currency}</span>
                <span className="text-muted">{e.title}</span>
                <span className="ml-auto font-mono text-xs text-faint">
                  {dateTime(e.ts)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </main>
  );
}
