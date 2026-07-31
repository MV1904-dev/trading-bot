"use client";

import { useMemo, useState } from "react";
import { useLive } from "@/lib/useLive";
import { dateTime, money, pnlClass, price, signed } from "@/lib/format";
import type { Trade } from "@/lib/types";

const PERIODS = [
  { key: "7", label: "7 dní" },
  { key: "30", label: "30 dní" },
  { key: "90", label: "90 dní" },
  { key: "all", label: "Všetko" },
];

export default function HistoryPage() {
  const { rows: trades } = useLive<Trade>("trades", (q) =>
    q.select("*").order("closed_at", { ascending: false }).limit(5000),
  );

  const [strategy, setStrategy] = useState("all");
  const [symbol, setSymbol] = useState("all");
  const [side, setSide] = useState("all");
  const [period, setPeriod] = useState("30");
  const [manual, setManual] = useState("all");

  const strategies = [...new Set(trades.map((t) => t.strategy))].sort();
  const symbols = [...new Set(trades.map((t) => t.symbol))].sort();

  const filtered = useMemo(() => {
    const cutoff =
      period === "all"
        ? 0
        : Date.now() - Number(period) * 86_400_000;
    return trades.filter((t) => {
      if (strategy !== "all" && t.strategy !== strategy) return false;
      if (symbol !== "all" && t.symbol !== symbol) return false;
      if (side !== "all" && t.side !== side) return false;
      if (manual === "manual" && !t.manual_close) return false;
      if (manual === "auto" && t.manual_close) return false;
      if (cutoff && new Date(t.closed_at ?? 0).getTime() < cutoff) return false;
      return true;
    });
  }, [trades, strategy, symbol, side, period, manual]);

  const sum = filtered.reduce((a, t) => a + (Number(t.pnl_usd) || 0), 0);

  function exportCsv() {
    const head = [
      "id", "strategia", "par", "smer", "qty", "vstup", "tp", "vystup",
      "otvorene", "zavrete", "hruby_pnl", "provizia", "funding", "spread",
      "cisty_pnl", "rucne_zatvorene",
    ];
    const rows = filtered.map((t) => [
      t.id, t.strategy, t.symbol, t.side, t.qty, t.entry_price, t.tp_price ?? "",
      t.close_price ?? "", t.opened_at, t.closed_at ?? "",
      t.gross_pnl_usd ?? "", t.commission_usd, t.funding_usd,
      t.spread_cost_usd ?? "", t.pnl_usd ?? "", t.manual_close ? "ano" : "nie",
    ]);
    // Oddeľovač je bodkočiarka a desatinná čiarka ostáva bodkou — takto to
    // slovenský Excel otvorí do stĺpcov bez importného sprievodcu.
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
    <main className="space-y-3">
      <h1 className="px-1 text-lg font-semibold">História obchodov</h1>

      <div className="card space-y-3">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <Select value={strategy} onChange={setStrategy} label="Stratégia"
            options={[["all", "Všetky"], ...strategies.map((s) => [s, s] as [string, string])]} />
          <Select value={symbol} onChange={setSymbol} label="Pár"
            options={[["all", "Všetky"], ...symbols.map((s) => [s, s] as [string, string])]} />
          <Select value={side} onChange={setSide} label="Smer"
            options={[["all", "Oba"], ["long", "Long"], ["short", "Short"]]} />
          <Select value={period} onChange={setPeriod} label="Obdobie"
            options={PERIODS.map((p) => [p.key, p.label] as [string, string])} />
          <Select value={manual} onChange={setManual} label="Stav"
            options={[["all", "Všetko"], ["auto", "Cez TP"], ["manual", "Ručne"]]} />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-zinc-400">
            {filtered.length} obchodov ·{" "}
            <span className={`font-mono ${pnlClass(sum)}`}>{signed(sum)}</span>
          </span>
          <button
            onClick={exportCsv}
            disabled={filtered.length === 0}
            className="btn-ghost ml-auto"
          >
            Export CSV
          </button>
        </div>
      </div>

      {filtered.length === 0 ? (
        <p className="card text-sm text-zinc-400">
          Žiadne obchody pre zvolený filter.
        </p>
      ) : (
        <div className="card">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Zavreté</th>
                  <th>Stratégia</th>
                  <th>Smer</th>
                  <th>Vstup → Výstup</th>
                  <th className="text-right">Provízia</th>
                  <th className="text-right">Funding</th>
                  <th className="text-right">Čistý P/L</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((t) => (
                  <tr key={t.id}>
                    <td className="text-zinc-500">
                      {t.id}
                      {t.manual_close && (
                        <span
                          title="Ručne zatvorené z dashboardu"
                          className="ml-1 text-amber-400"
                        >
                          ✋
                        </span>
                      )}
                    </td>
                    <td className="text-zinc-400">{dateTime(t.closed_at)}</td>
                    <td>{t.strategy}</td>
                    <td
                      className={
                        t.side === "long" ? "text-emerald-400" : "text-sky-400"
                      }
                    >
                      {t.side === "long" ? "LONG" : "SHORT"}{" "}
                      <span className="text-zinc-500">{money(t.qty, 0)}</span>
                    </td>
                    <td className="font-mono">
                      {price(t.entry_price)} → {price(t.close_price)}
                    </td>
                    <td className="text-right font-mono text-zinc-400">
                      −{money(t.commission_usd)}
                    </td>
                    <td
                      className={`text-right font-mono ${pnlClass(t.funding_usd)}`}
                    >
                      {signed(t.funding_usd)}
                    </td>
                    <td
                      className={`text-right font-mono ${pnlClass(t.pnl_usd)}`}
                    >
                      {signed(t.pnl_usd)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </main>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: [string, string][];
}) {
  return (
    <label className="block">
      <span className="card-title">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="input mt-1"
      >
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}
