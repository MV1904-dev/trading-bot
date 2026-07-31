"use client";

import { useLive } from "@/lib/useLive";
import { dateTime } from "@/lib/format";
import { Empty, Section } from "@/components/ui";
import type { CalendarEvent, DailyCycle } from "@/lib/types";

const BLACKOUT_MIN = 30;

export default function CalendarPage() {
  const { rows: events } = useLive<CalendarEvent>("calendar_events", (q) =>
    q.select("*").order("ts", { ascending: true }).limit(500),
  );
  const { rows: daily } = useLive<DailyCycle>("daily_cycles", (q) =>
    q.select("*").order("day", { ascending: false }).limit(120),
  );

  const now = Date.now();
  const upcoming = events.filter((e) => new Date(e.ts).getTime() >= now);
  const past = events.filter((e) => new Date(e.ts).getTime() < now).slice(-8).reverse();

  // Ticho vypisujeme len pre pracovné dni — víkendové riadky boli
  // predtým polovica zoznamu a nič nehovorili.
  const traded = new Set(daily.map((d) => d.day));
  const eventDays = new Set(events.map((e) => new Date(e.ts).toISOString().slice(0, 10)));
  const quiet: { day: string; reason: string }[] = [];
  for (let i = 1; i <= 30; i++) {
    const d = new Date(now - i * 86_400_000);
    const dow = d.getDay();
    if (dow === 0 || dow === 6) continue;
    const key = d.toISOString().slice(0, 10);
    if (traded.has(key)) continue;
    quiet.push({
      day: key,
      reason: eventDays.has(key) ? "blackout" : "bez cyklu",
    });
  }

  return (
    <main>
      <Section
        title="Nadchádzajúce"
        meta={upcoming.length ? `${upcoming.length}` : undefined}
        info={`Bot nevstupuje ±${BLACKOUT_MIN} minút okolo high-impact udalostí. Otvorené pozície bežia ďalej, blokujú sa len nové vstupy. Kalendár dopredu vidí spravidla na týždeň.`}
      >
        {upcoming.length === 0 ? (
          <Empty>Žiadne high-impact udalosti v načítanom kalendári.</Empty>
        ) : (
          upcoming.slice(0, 15).map((e) => {
            const t = new Date(e.ts).getTime();
            const soon = t - now < 6 * 3600_000;
            const from = new Date(t - BLACKOUT_MIN * 60_000);
            const to = new Date(t + BLACKOUT_MIN * 60_000);
            const hm = (d: Date) =>
              d.toLocaleTimeString("sk-SK", { hour: "2-digit", minute: "2-digit" });
            return (
              <div key={`${e.ts}-${e.title}`} className="row">
                <span className={e.currency === "USD" ? "chip-short" : "chip-long"}>
                  {e.currency}
                </span>
                <span className={`truncate ${soon ? "text-warn" : ""}`}>{e.title}</span>
                <span className="num ml-auto shrink-0 text-xs text-faint">
                  {hm(from)}–{hm(to)}
                </span>
                <span className="shrink-0 text-xs text-faint">
                  {new Date(e.ts).toLocaleDateString("sk-SK", { weekday: "short" })}
                </span>
              </div>
            );
          })
        )}
      </Section>

      <Section
        title="Dni bez obchodu"
        meta="30 dní, pracovné"
        info="Víkendy sa nevypisujú — trh je zatvorený a riadok by nič nehovoril. „Blackout“ znamená, že na ten deň padla high-impact udalosť."
      >
        {quiet.length === 0 ? (
          <Empty>Každý pracovný deň mal aspoň jeden zavretý obchod.</Empty>
        ) : (
          quiet.map((q) => (
            <div key={q.day} className="row">
              <span className="text-muted">
                {new Date(q.day).toLocaleDateString("sk-SK", {
                  weekday: "short", day: "2-digit", month: "2-digit",
                })}
              </span>
              <span className="ml-auto text-xs text-faint">{q.reason}</span>
            </div>
          ))
        )}
      </Section>

      {past.length > 0 && (
        <Section title="Posledné blackouty">
          {past.map((e) => (
            <div key={`${e.ts}-${e.title}`} className="row">
              <span className="text-xs text-faint">{e.currency}</span>
              <span className="truncate text-muted">{e.title}</span>
              <span className="ml-auto shrink-0 text-xs text-faint">
                {dateTime(e.ts)}
              </span>
            </div>
          ))}
        </Section>
      )}
    </main>
  );
}
