"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase";
import { dateTime, money, pnlClass, signed } from "@/lib/format";
import { DataStamp, RefreshButton, Tile } from "@/components/ui";
import { BLACKOUT_MIN, BOT_TZ, botToday, fxMarketClosed } from "@/lib/market";
import type { BotState, CalendarEvent, DailyCycle, Position } from "@/lib/types";

const STALE_MS = 3 * 60 * 1000;

/**
 * Horná časť prehľadu: riadok stavu s pauzou a pod ním dlaždice v troch
 * radoch — výsledok (celkom, dnes, výnos), kapitál (vklady, voľná marža,
 * float) a náklady s rizikom (swapy, dnešný blackout). Zadanie 15. 10. 2026.
 */
export default function Hero({
  state,
  positions,
  daily,
  events,
  onRefresh,
}: {
  state: BotState | null;
  positions: Position[];
  daily: DailyCycle[];
  events: CalendarEvent[];
  onRefresh?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (!state) {
    return <p className="py-8 text-center text-sm text-muted">Čakám na prvý stav bota.</p>;
  }

  // heartbeat_at = beží proces, updated_at = kedy vznikli DÁTA. Keď padne
  // príprava snapshotu, push vlákno ďalej odosiela posledný dobrý stav
  // s čerstvým heartbeatom — zamrznutie prezradí len vek dát.
  const now = Date.now();
  const dead = now - new Date(state.heartbeat_at).getTime() > STALE_MS;
  const frozen = now - new Date(state.updated_at).getTime() > STALE_MS;
  const stale = dead || frozen;
  const equity = state.equity ?? state.balance ?? 0;
  const deposits = state.deposits_net ?? 0;
  const roi = deposits > 0 ? ((equity - deposits) / deposits) * 100 : null;

  // Celkom = equity − vklady, teda realizované aj plávajúce. Výnos je
  // to isté v percentách vkladov. Bez vkladov sa nedá ani jedno: demo
  // archív ich hlási ako 0 (cash_flow k nemu nič nevracia) a celý zostatok
  // by vyzeral ako zisk.
  const total = deposits > 0 ? equity - deposits : null;

  const today = botToday(now);
  const todayRows = daily.filter((d) => d.day === today);
  const todayPnl = todayRows.reduce((a, d) => a + (Number(d.pnl_usd) || 0), 0);
  const floating = positions.reduce((a, p) => a + (Number(p.pnl_float) || 0), 0);
  const openSwap = positions.reduce((a, p) => a + (Number(p.funding_usd) || 0), 0);

  const ml = state.margin_level;
  const marginTone = ml != null && ml < 100 ? "text-neg" : ml != null && ml < 200 ? "text-warn" : "";

  // Blackout: najbližšie okno ±BLACKOUT_MIN okolo high-impact udalosti,
  // ktoré ešte neskončilo. calendar_events nesie len udalosti, z ktorých bot
  // blackout naozaj robí. Udalosti v rovnakom čase sú JEDNO okno (napr. PPI
  // a Retail Sales o 14:30), nie viac blackoutov. Keď dnes už žiadny nie je,
  // ukáže sa zajtrajší — večer je to užitočnejšie než „žiadny".
  const win = BLACKOUT_MIN * 60_000;
  const hm = (t: number) => new Date(t).toLocaleTimeString("sk-SK",
    { hour: "2-digit", minute: "2-digit", timeZone: BOT_TZ });
  const tomorrow = botToday(now + 86_400_000);
  const upcoming = events
    .map((e) => ({ ...e, t: new Date(e.ts).getTime() }))
    .filter((e) => e.t + win > now)
    .sort((a, b) => a.t - b.t);
  const next = upcoming[0];
  const nextDay = next ? botToday(next.t) : null;
  const group = next ? upcoming.filter((e) => e.t === next.t) : [];
  const what = next
    ? `${[...new Set(group.map((e) => e.currency))].join("/")} · ${next.title}`
      + (group.length > 1 ? ` +${group.length - 1}` : "")
    : undefined;
  const blackout =
    events.length === 0
      ? { label: "Blackout dnes", value: "—", tone: "text-faint", sub: "kalendár je prázdny" }
      : !next || (nextDay !== today && nextDay !== tomorrow)
        ? { label: "Blackout dnes", value: "žiadny", tone: "text-faint", sub: undefined }
        : next.t - win <= now
          ? { label: "Blackout teraz", value: `do ${hm(next.t + win)}`, tone: "text-warn", sub: what }
          : { label: nextDay === today ? "Blackout dnes" : "Blackout zajtra",
              value: `${hm(next.t - win)}–${hm(next.t + win)}`,
              tone: nextDay === today ? "text-warn" : "", sub: what };

  const tone = dead
    ? { dot: "bg-faint", text: "Bez spojenia" }
    : frozen
      ? { dot: "bg-neg", text: "Dáta zamrznuté" }
      : !state.broker_connected
        ? { dot: "bg-neg", text: "Broker odpojený" }
        : state.paused
          ? { dot: "bg-warn", text: "Pauza" }
          : { dot: "bg-pos", text: "Beží" };

  async function send(action: "pause" | "start") {
    setBusy(true);
    const supabase = createClient();
    const { data: { user } } = await supabase.auth.getUser();
    const { error } = await supabase.from("commands").insert({
      action,
      created_by: user?.id,
      params: action === "pause" ? { minutes: 60 } : null,
    });
    setBusy(false);
    setMsg(error ? `Nepodarilo sa: ${error.message}` : "Príkaz zaradený.");
  }

  return (
    <div className="pt-3">
      <div className="flex items-center gap-2">
        <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} />
        <span className="text-xs text-muted">{tone.text}</span>
        <DataStamp iso={state.updated_at} />
        {state.failsafe && (
          <span className="chip bg-neg/15 text-neg">G8 poistka</span>
        )}
        <span className="ml-auto flex items-center gap-1">
          {onRefresh && <RefreshButton onClick={onRefresh} />}
        </span>
        <button
          onClick={() => send(state.paused ? "start" : "pause")}
          disabled={busy}
          className="btn-quiet"
        >
          {state.paused ? "Spustiť" : "Pauza 60 min"}
        </button>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-2">
        <Tile label="Celkom" value={total == null ? "—" : signed(total)}
              tone={pnlClass(total)} />
        <Tile label="Dnes" value={signed(todayPnl)} tone={pnlClass(todayPnl)} />
        <Tile label="Výnos" tone={pnlClass(total)}
              value={roi == null ? "—" : `${signed(roi)} %`} />

        <Tile label="Vklady" value={deposits > 0 ? money(deposits) : "—"} />
        <Tile label="Voľná marža" value={money(state.free_margin)} tone={marginTone}
              sub={ml == null ? undefined : `marža ${ml.toFixed(0)} %`} />
        <Tile label="Float" value={signed(floating)} tone={pnlClass(floating)} />

        <Tile label="Swapy" value={signed(openSwap)} tone={pnlClass(openSwap)} />
        <Tile label={blackout.label} value={blackout.value} tone={blackout.tone}
              sub={blackout.sub} className="col-span-2" />
      </div>

      {state.blocked_reason && (
        <p className="mt-2 text-xs text-warn">Vstupy blokované: {state.blocked_reason}</p>
      )}
      {stale && (
        <p className="mt-2 text-xs text-muted">
          {dead
            ? "Posledný signál je starší než 3 minúty — čísla nemusia byť aktuálne."
            : "Bot sa hlási, ale dáta sa neobnovujú viac než 3 minúty — " +
              "čísla nižšie sú posledný známy stav, nie aktuálny."}
        </p>
      )}
      {!stale && !fxMarketClosed(now) && state.last_price == null && (
        <p className="mt-2 text-xs text-neg">
          Bot sa hlási, ale nemá cenu zo streamu — plávajúce P/L sa
          neprepočítava a nové vstupy nevznikajú.
        </p>
      )}
      {msg && <p className="mt-2 text-xs text-muted">{msg}</p>}
    </div>
  );
}
