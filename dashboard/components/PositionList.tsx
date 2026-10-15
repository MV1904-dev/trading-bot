"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase";
import { age, holdClass, holdMs, money, pnlClass, price, signed } from "@/lib/format";
import { Detail, Empty, ExpandableRow } from "@/components/ui";
import type { Position } from "@/lib/types";

/**
 * Pozície ako rozklikávacie riadky namiesto tabuľky. Zhrnutie má štyri
 * hodnoty a zmestí sa aj na 320 px širokú obrazovku; TP, funding a
 * zatvorenie sú v rozkliku.
 */
export default function PositionList({
  positions,
  limit,
}: {
  positions: Position[];
  limit?: number;
}) {
  const [confirming, setConfirming] = useState<Position | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const shown = limit ? positions.slice(0, limit) : positions;
  if (positions.length === 0) return <Empty>Žiadne otvorené pozície.</Empty>;

  async function close(p: Position) {
    setBusy(true);
    const supabase = createClient();
    const { data: { user } } = await supabase.auth.getUser();
    const { error } = await supabase.from("commands").insert({
      action: "close",
      position_id: p.id,
      created_by: user?.id,
    });
    setBusy(false);
    setConfirming(null);
    setMsg(error ? `Nepodarilo sa: ${error.message}` : `Zatváram #${p.id}…`);
  }

  return (
    <>
      {msg && <p className="mb-2 text-xs text-muted">{msg}</p>}

      {shown.map((p) => (
        <ExpandableRow
          key={p.id}
          summary={
            <>
              <span className={p.side === "long" ? "chip-long" : "chip-short"}>
                {p.side === "long" ? "L" : "S"}
              </span>
              <span className="num">{price(p.entry_price)}</span>
              <span className={`text-xs ${holdClass(holdMs(p.opened_at, null))}`}>
                {age(p.opened_at)}
              </span>
              <span className={`num ml-auto ${pnlClass(p.pnl_float)}`}>
                {signed(p.pnl_float)}
              </span>
            </>
          }
        >
          <Detail label="Objem" value={money(p.qty, 0)} />
          <Detail label="Take profit" value={price(p.tp_price)} />
          <Detail
            label="Swap za držanie"
            value={signed(p.funding_usd)}
            tone={pnlClass(p.funding_usd)}
          />
          <Detail label="Stratégia" value={p.strategy} />
          <div className="mt-2 text-right">
            <button onClick={() => setConfirming(p)} className="btn-quiet !text-neg">
              Zatvoriť
            </button>
          </div>
        </ExpandableRow>
      ))}

      {limit && positions.length > limit && (
        <p className="pt-2 text-xs text-faint">
          a ďalších {positions.length - limit}
        </p>
      )}

      {confirming && (
        <div
          className="fixed inset-0 z-30 flex items-end justify-center bg-black/60 p-4 sm:items-center"
          onClick={() => setConfirming(null)}
        >
          <div
            className="w-full max-w-sm rounded-xl bg-solid p-4 shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="font-medium">Zatvoriť #{confirming.id}?</h2>
            <p className="mt-1 text-sm text-muted">
              {confirming.side === "long" ? "Long" : "Short"}{" "}
              {money(confirming.qty, 0)} @ {price(confirming.entry_price)} ·{" "}
              <span className={pnlClass(confirming.pnl_float)}>
                {signed(confirming.pnl_float)}
              </span>
            </p>
            <p className="mt-2 text-xs text-faint">
              Zatvorí sa trhovým príkazom, takže cena sa môže líšiť. V histórii
              bude označené ako ručné.
            </p>
            <div className="mt-4 flex gap-2">
              <button onClick={() => setConfirming(null)} className="btn-ghost flex-1">
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
    </>
  );
}
