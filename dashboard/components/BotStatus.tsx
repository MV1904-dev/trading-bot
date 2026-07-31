"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase";
import { dateTime } from "@/lib/format";
import type { BotState } from "@/lib/types";

/** Heartbeat starší než 3 min = bot pravdepodobne nebeží (push je 1×/min). */
const STALE_MS = 3 * 60 * 1000;

export default function BotStatus({ state }: { state: BotState | null }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (!state) {
    return (
      <div className="card">
        <p className="text-sm text-zinc-400">
          Zatiaľ neprišiel žiadny stav — bot ešte nič nepushol.
        </p>
      </div>
    );
  }

  const stale = Date.now() - new Date(state.heartbeat_at).getTime() > STALE_MS;
  const paused = state.paused;

  const label = stale
    ? { text: "Bez spojenia", cls: "bg-zinc-700" }
    : !state.broker_connected
      ? { text: "Broker odpojený", cls: "bg-rose-600" }
      : paused
        ? { text: "Pauza", cls: "bg-amber-600" }
        : { text: "Beží", cls: "bg-emerald-600" };

  async function send(action: "pause" | "start") {
    setBusy(true);
    setMsg(null);
    const supabase = createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    const { error } = await supabase.from("commands").insert({
      action,
      created_by: user?.id,
      params: action === "pause" ? { minutes: 60 } : null,
    });
    setBusy(false);
    setMsg(
      error
        ? `Nepodarilo sa: ${error.message}`
        : "Príkaz zaradený — bot ho vyzdvihne do 5 s.",
    );
  }

  return (
    <div className="card">
      <div className="flex flex-wrap items-center gap-3">
        <span
          className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs
                      font-medium text-white ${label.cls}`}
        >
          {label.text}
        </span>
        {state.failsafe && (
          <span className="rounded-full bg-rose-950 px-2.5 py-1 text-xs text-rose-300">
            G8 poistka aktívna
          </span>
        )}
        <span className="text-xs text-zinc-500">
          heartbeat {dateTime(state.heartbeat_at)}
        </span>

        <div className="ml-auto flex gap-2">
          {paused ? (
            <button
              onClick={() => send("start")}
              disabled={busy}
              className="btn-primary"
            >
              Spustiť vstupy
            </button>
          ) : (
            <button
              onClick={() => send("pause")}
              disabled={busy}
              className="btn-warn"
            >
              Pauza 60 min
            </button>
          )}
        </div>
      </div>

      {state.blocked_reason && (
        <p className="mt-3 text-sm text-amber-300">
          Vstupy blokované: {state.blocked_reason}
        </p>
      )}
      {paused && state.paused_until && (
        <p className="mt-1 text-sm text-zinc-400">
          Pauza do {dateTime(state.paused_until)}
        </p>
      )}
      {stale && (
        <p className="mt-3 text-sm text-zinc-400">
          Posledný signál je starší než 3 minúty. Údaje nižšie sú z posledného
          úspešného pushu, nie z aktuálneho stavu.
        </p>
      )}
      {msg && <p className="mt-3 text-sm text-zinc-300">{msg}</p>}
    </div>
  );
}
