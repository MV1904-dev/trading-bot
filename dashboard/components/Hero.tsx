"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase";
import { dateTime, money, pnlClass, price, signed } from "@/lib/format";
import { Tile } from "@/components/ui";
import type { BotState, DailyCycle, Position } from "@/lib/types";

const STALE_MS = 3 * 60 * 1000;

/**
 * Horná tretina obrazovky: stav, jedno hlavné číslo, tri metriky.
 * Nahrádza pôvodných päť samostatných kariet, ktoré mali rovnakú váhu
 * a tlačili obsah pod okraj.
 */
export default function Hero({
  state,
  positions,
  daily,
}: {
  state: BotState | null;
  positions: Position[];
  daily: DailyCycle[];
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (!state) {
    return <p className="py-8 text-center text-sm text-muted">Čakám na prvý stav bota.</p>;
  }

  const stale = Date.now() - new Date(state.heartbeat_at).getTime() > STALE_MS;
  const equity = state.equity ?? state.balance ?? 0;
  const deposits = state.deposits_net ?? 0;
  const roi = deposits > 0 ? ((equity - deposits) / deposits) * 100 : null;

  const today = new Date().toISOString().slice(0, 10);
  const todayRows = daily.filter((d) => d.day === today);
  const todayPnl = todayRows.reduce((a, d) => a + (Number(d.pnl_usd) || 0), 0);
  const floating = positions.reduce((a, p) => a + (Number(p.pnl_float) || 0), 0);

  const tone = stale
    ? { dot: "bg-faint", text: "Bez spojenia" }
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
        <span className="text-xs text-muted">
          {tone.text} · {dateTime(state.heartbeat_at)?.slice(-5)}
        </span>
        {state.failsafe && (
          <span className="chip bg-neg/15 text-neg">G8 poistka</span>
        )}
        <button
          onClick={() => send(state.paused ? "start" : "pause")}
          disabled={busy}
          className="btn-quiet ml-auto"
        >
          {state.paused ? "Spustiť" : "Pauza 60 min"}
        </button>
      </div>

      <div className="mt-3 flex items-end gap-2.5">
        <span className="num text-[29px] font-medium leading-none tracking-tight">
          {money(equity)}
        </span>
        {roi != null && (
          <span className={`num pb-0.5 text-sm ${pnlClass(equity - deposits)}`}>
            {roi >= 0 ? "+" : ""}{roi.toFixed(2)} %
          </span>
        )}
      </div>
      <p className="mt-1 text-xs text-faint">
        vklad {money(deposits)} · realizovaná {money(state.balance)}
      </p>

      <div className="mt-3 grid grid-cols-3 gap-2">
        <Tile label="Dnes" value={signed(todayPnl)} tone={pnlClass(todayPnl)} />
        <Tile label="Float" value={signed(floating)} tone={pnlClass(floating)} />
        <Tile
          label="Marža"
          value={state.margin_level == null ? "—" : `${state.margin_level.toFixed(0)} %`}
          tone={
            state.margin_level != null && state.margin_level < 100
              ? "text-neg"
              : state.margin_level != null && state.margin_level < 200
                ? "text-warn"
                : ""
          }
        />
      </div>

      <div className="mt-3 flex items-center gap-2 text-xs text-faint">
        <span>{state.symbol ?? "EURUSD"}</span>
        <span className="num text-ink">{price(state.last_price)}</span>
        <Band state={state} />
      </div>

      {state.blocked_reason && (
        <p className="mt-2 text-xs text-warn">Vstupy blokované: {state.blocked_reason}</p>
      )}
      {stale && (
        <p className="mt-2 text-xs text-muted">
          Posledný signál je starší než 3 minúty — čísla nemusia byť aktuálne.
        </p>
      )}
      {msg && <p className="mt-2 text-xs text-muted">{msg}</p>}
    </div>
  );
}

function Band({ state }: { state: BotState }) {
  const lo = state.band_low;
  const hi = state.band_high;
  const px = state.last_price;
  if (lo == null || hi == null || px == null) return null;
  const pos = ((Math.min(Math.max(px, lo), hi) - lo) / (hi - lo)) * 100;
  const out = px < lo || px > hi;
  return (
    <span className="relative ml-auto h-1 w-24 rounded-full bg-surface">
      <span
        className={`absolute top-1/2 h-2.5 w-0.5 -translate-y-1/2 rounded ${out ? "bg-neg" : "bg-pos"}`}
        style={{ left: `calc(${pos}% - 1px)` }}
      />
    </span>
  );
}
