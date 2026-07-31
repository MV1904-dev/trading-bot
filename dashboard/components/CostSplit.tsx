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
  const rows: [string, (s: SideCosts) => string, (s: SideCosts) => string][] = [
    ["Obchodov", (s) => String(s.trades), () => "text-ink"],
    ["Provízie", (s) => `−${money(s.commission)}`, () => "text-muted"],
    ["Spread", (s) => `−${money(s.spread)}`, () => "text-muted"],
    [
      "Funding",
      (s) => signed(s.funding),
      (s) => (s.funding < 0 ? "text-neg" : s.funding > 0 ? "text-pos" : "text-faint"),
    ],
    [
      "Náklady spolu",
      (s) => `−${money(s.commission + s.spread - Math.min(s.funding, 0))}`,
      () => "text-ink font-medium",
    ],
  ];

  const totalFunding = long.funding + short.funding;
  const longShare =
    totalFunding === 0 ? null : (long.funding / totalFunding) * 100;

  return (
    <div>
      <div className="card-title mb-2">Náklady: long vs short</div>

      <div className="table-wrap">
        <table className="data !min-w-[360px]">
          <thead>
            <tr>
              <th />
              <th className="text-right">
                <span className="text-long">LONG</span>
              </th>
              <th className="text-right">
                <span className="text-short">SHORT</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, val, cls]) => (
              <tr key={label}>
                <td className="text-muted">{label}</td>
                <td className={`text-right font-mono ${cls(long)}`}>
                  {long.trades === 0 ? "—" : val(long)}
                </td>
                <td className={`text-right font-mono ${cls(short)}`}>
                  {short.trades === 0 ? "—" : val(short)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <FundingBar long={long.funding} short={short.funding} />

      <p className="mt-2 text-xs text-faint">
        {longShare == null ? (
          "Funding zatiaľ nebol účtovaný ani na jednej strane."
        ) : (
          <>
            Longy nesú <strong>{Math.abs(longShare).toFixed(0)} %</strong>{" "}
            celkového funding nákladu. Swap sa účtuje len pozíciám otvoreným
            cez denný rollover — dotklo sa to{" "}
            {long.fundingCharged + short.fundingCharged} z{" "}
            {long.trades + short.trades} obchodov.
          </>
        )}
      </p>
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
      <div className="flex h-2 overflow-hidden rounded-full bg-line">
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
