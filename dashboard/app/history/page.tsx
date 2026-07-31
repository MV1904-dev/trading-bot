"use client";

import { useMemo, useState } from "react";
import { useLive } from "@/lib/useLive";
import {
  dateTime, heldFor, holdClass, holdMs, money, pnlClass, price, signed,
} from "@/lib/format";
import { Detail, Empty, ExpandableRow, Section } from "@/components/ui";
import type { Trade } from "@/lib/types";

const PERIODS: [string, string][] = [
  ["7", "7 dní"],
  ["30", "30 dní"],
  ["90", "90 dní"],
  ["all", "Všetko"],
];

export default function HistoryPage() {
  const { rows: trades } = useLive<Trade>("trades", (q) =>
    q.select("*").order("closed_at", { ascending: false }).limit(5000),
  );

  const [strategy, setStrategy] = useState("all");
  const [side, setSide] = useState("all");
  const [period, setPeriod] = useState("30");
  const [manual, setManual] = useState("all");
  const [filtersOpen, setFiltersOpen] = useState(false);

  const strategies = [...new Set(trades.map((t) => t.strategy))].sort();

  const filtered = useMemo(() => {
    const cutoff = period === "all" ? 0 : Date.now() - Number(period) * 86_400_000;
    return trades.filter((t) => {
      if (strategy !== "all" && t.strategy !== strategy) return false;
      if (side !== "all" && t.side !== side) return false;
      if (manual === "manual" && !t.manual_close) return false;
      if (manual === "auto" && t.manual_close) return false;
      if (cutoff && new Date(t.closed_at ?? 0).getTime() < cutoff) return false;
      return true;
    });
  }, [trades, strategy, side, period, manual]);

  const sum = filtered.reduce((a, t) => a + (Number(t.pnl_usd) || 0), 0);
  const active =
    [strategy, side, manual].filter((v) => v !== "all").length +
    (period !== "30" ? 1 : 0);

  function exportCsv() {
    const head = [
      "id", "strategia", "par", "smer", "qty", "vstup", "tp", "vystup",
      "otvorene", "zavrete", "drzane_h", "hruby_pnl", "provizia", "funding",
      "spread", "cisty_pnl", "rucne_zatvorene",
    ];
    const rows = filtered.map((t) => [
      t.id, t.strategy, t.symbol, t.side, t.qty, t.entry_price, t.tp_price ?? "",
      t.close_price ?? "", t.opened_at, t.closed_at ?? "",
      t.closed_at ? (holdMs(t.opened_at, t.closed_at) / 3_600_000).toFixed(2) : "",
      t.gross_pnl_usd ?? "", t.commission_usd, t.funding_usd,
      t.spread_cost_usd ?? "", t.pnl_usd ?? "", t.manual_close ? "ano" : "nie",
    ]);
    // Bodkočiarka ako oddeľovač — slovenský Excel to otvorí do stĺpcov
    // bez importného sprievodcu.
    const csv = [head, ...rows]
      .map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(";"))
      .join("\n");
    const url = URL.createObjectURL(
      new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `obchody-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <main>
      <Section>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setFiltersOpen((v) => !v)}
            className="btn-quiet"
            aria-expanded={filtersOpen}
          >
            Filtre{active > 0 ? ` (${active})` : ""}
          </button>
          <span className="text-xs text-muted">
            {filtered.length} obchodov ·{" "}
            <span className={`num ${pnlClass(sum)}`}>{signed(sum)}</span>
          </span>
          <button
            onClick={exportCsv}
            disabled={filtered.length === 0}
            className="btn-quiet ml-auto"
          >
            CSV
          </button>
        </div>

        {filtersOpen && (
          <div className="mt-2 grid grid-cols-2 gap-2">
            <Pick label="Stratégia" value={strategy} onChange={setStrategy}
              options={[["all", "Všetky"], ...strategies.map((s) => [s, s] as [string, string])]} />
            <Pick label="Smer" value={side} onChange={setSide}
              options={[["all", "Oba"], ["long", "Long"], ["short", "Short"]]} />
            <Pick label="Obdobie" value={period} onChange={setPeriod} options={PERIODS} />
            <Pick label="Stav" value={manual} onChange={setManual}
              options={[["all", "Všetko"], ["auto", "Cez TP"], ["manual", "Ručne"]]} />
          </div>
        )}
      </Section>

      <Section
        title="Obchody"
        info="Klepnutím na riadok sa rozbalia ceny, provízia, funding a doba držania. Ručne zatvorené obchody majú pri čísle značku. Export CSV rešpektuje nastavené filtre."
      >
        {filtered.length === 0 ? (
          <Empty>Žiadne obchody pre zvolený filter.</Empty>
        ) : (
          filtered.map((t) => (
            <ExpandableRow
              key={t.id}
              summary={
                <>
                  <span className={t.side === "long" ? "chip-long" : "chip-short"}>
                    {t.side === "long" ? "L" : "S"}
                  </span>
                  <span className="text-xs text-muted">
                    {dateTime(t.closed_at)}
                  </span>
                  <span className={`text-xs ${holdClass(holdMs(t.opened_at, t.closed_at))}`}>
                    {heldFor(t.opened_at, t.closed_at)}
                  </span>
                  {t.manual_close && (
                    <span className="text-[10px] text-warn">ručne</span>
                  )}
                  <span className={`num ml-auto ${pnlClass(t.pnl_usd)}`}>
                    {signed(t.pnl_usd)}
                  </span>
                </>
              }
            >
              <Detail label="Objem" value={money(t.qty, 0)} />
              <Detail
                label="Vstup → výstup"
                value={`${price(t.entry_price)} → ${price(t.close_price)}`}
              />
              <Detail label="Hrubý P/L" value={signed(t.gross_pnl_usd)} />
              <Detail label="Provízia" value={`−${money(t.commission_usd)}`} />
              <Detail
                label="Funding"
                value={signed(t.funding_usd)}
                tone={pnlClass(t.funding_usd)}
              />
              <Detail label="Stratégia" value={t.strategy} />
            </ExpandableRow>
          ))
        )}
      </Section>
    </main>
  );
}

function Pick({
  label, value, onChange, options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: [string, string][];
}) {
  return (
    <label className="block">
      <span className="text-xs text-muted">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="input mt-1 !py-1.5 !text-sm"
      >
        {options.map(([v, l]) => (
          <option key={v} value={v}>{l}</option>
        ))}
      </select>
    </label>
  );
}
