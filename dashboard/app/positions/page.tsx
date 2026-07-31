"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase";
import { useLive } from "@/lib/useLive";
import {
  age, holdClass, holdMs, money, pnlClass, price, signed,
} from "@/lib/format";
import type { Position } from "@/lib/types";

export default function PositionsPage() {
  const { rows: positions } = useLive<Position>("positions", (q) =>
    q.select("*").order("id", { ascending: true }),
  );
  const [confirming, setConfirming] = useState<Position | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const floating = positions.reduce((a, p) => a + (Number(p.pnl_float) || 0), 0);
  // Funding je zatiaľ naakumulovaný celkovo; denná sadzba je jeho podiel
  // na veku pozície. Pri pozíciách mladších než deň je to nula, nie odhad.
  const fundingPerDay = positions.reduce((a, p) => {
    const days =
      (Date.now() - new Date(p.opened_at).getTime()) / 86_400_000;
    return a + (days >= 1 ? (Number(p.funding_usd) || 0) / days : 0);
  }, 0);

  async function close(p: Position) {
    setBusy(true);
    const supabase = createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    const { error } = await supabase.from("commands").insert({
      action: "close",
      position_id: p.id,
      created_by: user?.id,
    });
    setBusy(false);
    setConfirming(null);
    setMsg(
      error
        ? `Nepodarilo sa: ${error.message}`
        : `Príkaz na zatvorenie #${p.id} zaradený — bot ho vyzdvihne do 5 s.`,
    );
  }

  return (
    <main className="space-y-3">
      <h1 className="px-1 text-lg font-semibold">Otvorené pozície</h1>

      <div className="grid grid-cols-2 gap-3">
        <div className="card">
          <div className="card-title">Floating P/L</div>
          <div className={`mt-1 font-mono text-xl ${pnlClass(floating)}`}>
            {signed(floating)}
          </div>
        </div>
        <div className="card">
          <div className="card-title">Funding</div>
          <div className={`mt-1 font-mono text-xl ${pnlClass(fundingPerDay)}`}>
            {signed(fundingPerDay)} <span className="text-sm">/ deň</span>
          </div>
        </div>
      </div>

      {msg && <p className="card text-sm text-ink">{msg}</p>}

      {positions.length === 0 ? (
        <p className="card text-sm text-muted">Žiadne otvorené pozície.</p>
      ) : (
        <div className="card">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Stratégia</th>
                  <th>Smer</th>
                  <th>Vstup → TP</th>
                  <th>Vek</th>
                  <th className="text-right">Funding</th>
                  <th className="text-right">P/L</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.id}>
                    <td className="text-faint">{p.id}</td>
                    <td>{p.strategy}</td>
                    <td>
                      <span
                        className={
                          p.side === "long"
                            ? "text-long"
                            : "text-short"
                        }
                      >
                        {p.side === "long" ? "LONG" : "SHORT"}
                      </span>
                      <span className="ml-1 text-faint">
                        {money(p.qty, 0)}
                      </span>
                    </td>
                    <td className="font-mono">
                      {price(p.entry_price)} → {price(p.tp_price)}
                    </td>
                    <td className={holdClass(holdMs(p.opened_at, null))}>
                      {age(p.opened_at)}
                    </td>
                    <td
                      className={`text-right font-mono ${pnlClass(p.funding_usd)}`}
                    >
                      {signed(p.funding_usd)}
                    </td>
                    <td
                      className={`text-right font-mono ${pnlClass(p.pnl_float)}`}
                    >
                      {signed(p.pnl_float)}
                    </td>
                    <td className="text-right">
                      <button
                        onClick={() => setConfirming(p)}
                        className="btn-ghost !px-2 !py-1 text-xs"
                      >
                        Zatvoriť
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {confirming && (
        <div
          className="fixed inset-0 z-30 flex items-end justify-center bg-black/70 p-4 sm:items-center"
          onClick={() => setConfirming(null)}
        >
          <div className="card w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
            <h2 className="font-semibold">Zatvoriť pozíciu #{confirming.id}?</h2>
            <p className="mt-2 text-sm text-muted">
              {confirming.strategy} · {confirming.side === "long" ? "LONG" : "SHORT"}{" "}
              {money(confirming.qty, 0)} @ {price(confirming.entry_price)}
            </p>
            <p className={`mt-1 font-mono ${pnlClass(confirming.pnl_float)}`}>
              {signed(confirming.pnl_float)}
            </p>
            <p className="mt-3 text-xs text-faint">
              Zatvorí sa trhovým príkazom, takže výsledná cena sa môže líšiť od
              aktuálnej. Obchod bude v histórii označený ako ručne zatvorený.
            </p>
            <div className="mt-4 flex gap-2">
              <button
                onClick={() => setConfirming(null)}
                className="btn-ghost flex-1"
              >
                Späť
              </button>
              <button
                onClick={() => close(confirming)}
                disabled={busy}
                className="btn-danger flex-1"
              >
                {busy ? "Odosielam…" : "Zatvoriť"}
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
