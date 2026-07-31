"use client";

import { money, signed } from "@/lib/format";
import type { Trade } from "@/lib/types";

export type SideCosts = {
  trades: number;
  commission: number;
  spread: number;
  funding: number;
  spreadKnown: number;
  fundingCharged: number;
};

export function emptySide(): SideCosts {
  return {
    trades: 0,
    commission: 0,
    spread: 0,
    funding: 0,
    spreadKnown: 0,
    fundingCharged: 0,
  };
}

export function addTrade(acc: SideCosts, t: Trade) {
  acc.trades += 1;
  acc.commission += Number(t.commission_usd) || 0;
  acc.funding += Number(t.funding_usd) || 0;
  if (Number(t.funding_usd)) acc.fundingCharged += 1;
  if (t.spread_cost_usd != null) {
    acc.spread += Number(t.spread_cost_usd);
    acc.spreadKnown += 1;
  }
}

/**
 * Náklady zvlášť pre long a short.
 *
 * Zmysel je funding: swap sa účtuje len pozíciám, ktoré prežijú denný
 * rollover, a jeho znamienko závisí od úrokového diferenciálu meny — takže
 * jedna strana ho typicky platí a druhá skoro nie. V spoločnom súčte to
 * zanikne, tu je to vidieť.
 */
export default function CostSplit({
  long,
  short,
}: {
  long: SideCosts;
  short: SideCosts;
}) {
  const totalFunding = long.funding + short.funding;
  const longShare =
    totalFunding === 0 ? null : (long.funding / totalFunding) * 100;

  const rows: [string, (s: SideCosts) => string, (s: SideCosts) => string][] = [
    ["Obchodov", (s) => String(s.trades), () => ""],
    ["Provízie", (s) => `−${money(s.commission)}`, () => "text-muted"],
    ["Spread", (s) => `−${money(s.spread)}`, () => "text-muted"],
    ["Funding", (s) => signed(s.funding),
      (s) => (s.funding < 0 ? "text-neg" : s.funding > 0 ? "text-pos" : "text-faint")],
    ["Spolu", (s) => `−${money(s.commission + s.spread - Math.min(s.funding, 0))}`,
      () => "font-medium"],
  ];

  return (
    <div>
      <div className="mb-1 flex text-xs text-faint">
        <span className="flex-1">Náklady</span>
        <span className="w-20 text-right text-long">Long</span>
        <span className="w-20 text-right text-short">Short</span>
      </div>
      {rows.map(([label, val, cls]) => (
        <div key={label} className="row !py-1.5 text-xs">
          <span className="flex-1 text-muted">{label}</span>
          <span className={`num w-20 text-right ${cls(long)}`}>
            {long.trades === 0 ? "—" : val(long)}
          </span>
          <span className={`num w-20 text-right ${cls(short)}`}>
            {short.trades === 0 ? "—" : val(short)}
          </span>
        </div>
      ))}

      <FundingBar long={long.funding} short={short.funding} />

      {longShare != null && (
        <p className="mt-2 text-xs text-faint">
          Longy nesú {Math.abs(longShare).toFixed(0)} % funding nákladu. Swap sa
          účtuje len pozíciám cez denný rollover — dotklo sa to{" "}
          {long.fundingCharged + short.fundingCharged} z{" "}
          {long.trades + short.trades} obchodov.
        </p>
      )}
    </div>
  );
}

/** Pomer funding nákladu medzi stranami. Kreslíme absolútne hodnoty. */
function FundingBar({ long, short }: { long: number; short: number }) {
  const l = Math.abs(long);
  const s = Math.abs(short);
  const total = l + s;
  if (total === 0) return null;

  return (
    <div className="mt-3">
      <div className="flex h-2 overflow-hidden rounded-full bg-surface">
        <div className="bg-long" style={{ width: `${(l / total) * 100}%` }} />
        <div className="bg-short" style={{ width: `${(s / total) * 100}%` }} />
      </div>
      <div className="mt-1.5 flex justify-between text-xs">
        <span className="text-long">
          long {((l / total) * 100).toFixed(0)} %
        </span>
        <span className="text-short">
          short {((s / total) * 100).toFixed(0)} %
        </span>
      </div>
    </div>
  );
}
