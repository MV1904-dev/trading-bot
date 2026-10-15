"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase";
import { dateTime, money, pnlClass, signed } from "@/lib/format";
import { DataStamp, RefreshButton, Tile } from "@/components/ui";
import { BLACKOUT_MIN, BOT_TZ, botToday, fxMarketClosed } from "@/lib/market";
import type { BotState, CalendarEvent, DailyCycle, Position } from "@/lib/types";

const STALE_MS = 3 * 60 * 1000;

/**
 * Horná časť prehľadu: riadok stavu s pauzou a pod ním dlaždice 3 × 3 —
 * výsledok (celkom, dnes, výnos), kapitál (vklady, voľná marža, float)
 * a realizovaný zisk, swapy a najbližší blackout. Zadanie 15. 10. 2026.
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
  const deposits = state.deposits_net ?? 0;

  // Rozklad výsledku (zadanie 15. 10. 2026): Celkom = Realizovaný + Float
  // + Swapy. Equity zo snapshotu je balance + cenový float, naakumulovaný
  // swap otvorených pozícií v ňom nie je — broker ho strhne až pri
  // zatvorení, ale je to už vzniknutý náklad, tak patrí do celku.
  // Bez vkladov (demo archív ich hlási ako 0) sa nedá nič z toho.
  const realized = deposits > 0 && state.balance != null ? state.balance - deposits : null;

  const today = botToday(now);
  const todayRows = daily.filter((d) => d.day === today);
  const todayPnl = todayRows.reduce((a, d) => a + (Number(d.pnl_usd) || 0), 0);
  const floating = positions.reduce((a, p) => a + (Number(p.pnl_float) || 0), 0);
  const openSwap = positions.reduce((a, p) => a + (Number(p.funding_usd) || 0), 0);
  const total = realized == null ? null : realized + floating + openSwap;
  const roi = total == null ? null : (total / deposits) * 100;

  // Marža zo zložiek, nie zo stavu bota — overené proti aplikácii brokera
  // 15. 10. 22:45 (159 %, voľná 4 881,72, použitá 8 333,25):
  //  - equity bota je balance + cenový float, chýba v nej naakumulovaný swap
  //    otvorených pozícií; tu sa pripočíta,
  //  - bot sčíta usedMargin všetkých pozícií, ale cTrader pri protismerných
  //    pozíciách na jednom páre účtuje maržu len z väčšej strany (hedging).
  //    Pri 25 long / 3 short je to 25 × 333,33 = 8 333,25, nie 9 333,24.
  const qtyL = positions.reduce((a, p) => a + (p.side === "long" ? Number(p.qty) || 0 : 0), 0);
  const qtyS = positions.reduce((a, p) => a + (p.side === "short" ? Number(p.qty) || 0 : 0), 0);
  const usedSum = Number(state.used_margin) || 0;
  const used = qtyL + qtyS > 0 ? (usedSum * Math.max(qtyL, qtyS)) / (qtyL + qtyS) : 0;
  const eqTrue = state.balance != null ? state.balance + floating + openSwap : null;
  const freeMargin = eqTrue == null ? state.free_margin : eqTrue - used;
  const ml = eqTrue != null && used > 0 ? (eqTrue / used) * 100 : state.margin_level;
  const marginTone = ml != null && ml < 100 ? "text-neg" : ml != null && ml < 200 ? "text-warn" : "";

  // Blackout: začiatok najbližšieho okna ±BLACKOUT_MIN okolo high-impact
  // udalosti, ktoré ešte neskončilo (calendar_events = len udalosti, z ktorých
  // bot blackout naozaj robí). Bez textov (zadanie 15. 10. 2026): dnes žlto,
  // iný deň sivo a v zátvorke počet dní; počas okna "do HH:MM".
  const win = BLACKOUT_MIN * 60_000;
  const hm = (t: number) => new Date(t).toLocaleTimeString("sk-SK",
    { hour: "2-digit", minute: "2-digit", timeZone: BOT_TZ });
  const dayNo = (t: number) => Date.parse(`${botToday(t)}T00:00:00Z`) / 86_400_000;
  const next = events
    .map((e) => new Date(e.ts).getTime())
    .filter((t) => t + win > now)
    .sort((a, b) => a - b)[0];
  const inDays = next == null ? null : dayNo(next) - dayNo(now);
  const blackout =
    next == null
      ? { value: "—", tone: "text-faint" }
      : next - win <= now
        ? { value: `do ${hm(next + win)}`, tone: "text-warn" }
        : inDays === 0
          ? { value: hm(next - win), tone: "text-warn" }
          : { value: `${hm(next - win)} (${inDays})`, tone: "text-faint" };

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

      {/* 1. účet: koľko som vložil → koľko je výsledok → v percentách
          2. z čoho sa výsledok skladá: Realizovaný + Float + Swapy = Celkom
          3. teraz: dnešok, rezerva marže, najbližší blackout */}
      <div className="mt-3 grid grid-cols-3 gap-2">
        <Tile label="Vklady" value={deposits > 0 ? money(deposits, 0) : "—"} />
        <Tile label="Celkom" value={total == null ? "—" : signed(total, 0)}
              tone={pnlClass(total)} />
        <Tile label="Výnos" tone={pnlClass(total)}
              value={roi == null ? "—" : `${signed(roi)} %`} />

        <Tile label="Realizovaný" value={realized == null ? "—" : signed(realized, 0)}
              tone={pnlClass(realized)} />
        <Tile label="Float" value={signed(floating, 0)} tone={pnlClass(floating)} />
        <Tile label="Swapy" value={signed(openSwap, 0)} tone={pnlClass(openSwap)} />

        <Tile label="Dnes" value={signed(todayPnl, 0)} tone={pnlClass(todayPnl)} />
        <Tile label="Voľná marža" value={money(freeMargin, 0)} tone={marginTone}
              sub={ml == null ? undefined : `marža ${ml.toFixed(0)} %`} />
        <Tile label="Blackout" value={blackout.value} tone={blackout.tone} />
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
