"use client";

import { money, pnlClass, signed } from "@/lib/format";
import type { BotState } from "@/lib/types";

/**
 * Návratnosť vloženého kapitálu.
 *
 * Menovateľ je čistý vklad z cTrader cash flow histórie, nie balance —
 * balance už obsahuje zisk, takže by sa ním výnos delil sám sebou a
 * návratnosť by vychádzala nižšia, čím viac by bot zarobil.
 */
export default function Roi({
  state,
  firstTradeAt,
}: {
  state: BotState | null;
  firstTradeAt: string | null;
}) {
  const deposits = state?.deposits_net ?? null;
  const equity = state?.equity ?? state?.balance ?? null;
  if (deposits == null || deposits <= 0 || equity == null) return null;

  const profit = equity - deposits;
  const roi = (profit / deposits) * 100;

  // Anualizácia má zmysel až od nejakej histórie; pri pár dňoch by z
  // dobrého týždňa spravila stovky percent ročne.
  const days = firstTradeAt
    ? (Date.now() - new Date(firstTradeAt).getTime()) / 86_400_000
    : 0;
  const annual = days >= 14 ? (Math.pow(1 + roi / 100, 365 / days) - 1) * 100 : null;

  return (
    <div className="card">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="card-title">Návratnosť vloženého kapitálu</span>
        <span className="text-xs text-faint">
          vklad {money(deposits)} €
          {state?.deposits_count ? ` (${state.deposits_count}×)` : ""}
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <span className={`font-mono text-3xl ${pnlClass(profit)}`}>
          {roi >= 0 ? "+" : ""}
          {roi.toFixed(2)} %
        </span>
        <span className={`font-mono ${pnlClass(profit)}`}>
          {signed(profit)} €
        </span>
      </div>

      <dl className="mt-3 grid grid-cols-3 gap-3 text-sm">
        <div>
          <dt className="card-title">Vklad</dt>
          <dd className="mt-0.5 font-mono">{money(deposits)}</dd>
        </div>
        <div>
          <dt className="card-title">Aktuálne</dt>
          <dd className="mt-0.5 font-mono">{money(equity)}</dd>
        </div>
        <div>
          <dt className="card-title">p.a.</dt>
          <dd className="mt-0.5 font-mono">
            {annual == null ? "—" : `${annual >= 0 ? "+" : ""}${annual.toFixed(0)} %`}
          </dd>
        </div>
      </dl>

      <p className="mt-3 text-xs text-faint">
        {annual == null ? (
          <>
            Anualizovaný výnos ukážem až po dvoch týždňoch obchodovania —
            skôr by z jedného dobrého týždňa spravil nezmyselné číslo.
          </>
        ) : (
          <>
            p.a. je extrapolácia z {days.toFixed(0)} dní, nie predikcia.
          </>
        )}{" "}
        Zahŕňa aj neuzavreté pozície (floating), takže sa hýbe s kurzom.
      </p>
    </div>
  );
}
