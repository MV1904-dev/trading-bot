"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Empty, Section } from "@/components/ui";
import { money, pnlClass, signed } from "@/lib/format";
import type { BotState, Position } from "@/lib/types";

/**
 * Súťaž: živý grid proti stratégii Strážca na virtuálnom účte.
 *
 * Porovnáva sa ZMENA od štartu virtuálneho účtu, nie absolútne čísla:
 * live si nesie pozície otvorené pred súťažou (23 L / 6 S k 16. 10.),
 * virtuálny účet začal prázdny s rovnakým kapitálom (15 200 €). Equity
 * na oboch stranách = zostatok + float, bez naakumulovaného swapu
 * otvorených pozícií — ten by live penalizoval za staré pozície; swap sa
 * do výsledku dostane, keď sa pozícia zavrie.
 */

export type Env = "live" | "paper";
export type Snap = { env: Env; ts: string; equity: number };
export type TradeRow = { env: Env; pnl_usd: number | null; closed_at: string };
export type PosRow = Position & { env: Env };

const COLOR: Record<Env, string> = { live: "var(--ink)", paper: "#7c3aed" };
const NAME: Record<Env, string> = { live: "Live grid", paper: "Strážca (sim)" };

export default function Competition({ live, paper, trades, positions, snaps }: {
  live: BotState | null; paper: BotState | null; trades: TradeRow[];
  positions: PosRow[]; snaps: Snap[];
}) {
  const start = (paper?.config?.started_at as string | undefined) ?? null;
  if (!paper || !start) {
    return (
      <main>
        <Section title="Súťaž">
          <Empty>Virtuálny účet ešte nebeží — porovnanie sa objaví po jeho štarte.</Empty>
        </Section>
      </main>
    );
  }

  const side = (env: Env, st: BotState | null) => {
    const pos = positions.filter((p) => p.env === env);
    const tr = trades.filter((t) => t.env === env);
    const sn = snaps.filter((s) => s.env === env);
    const eqNow = st?.equity != null ? Number(st.equity) : null;
    const eqStart = env === "paper" ? Number(st?.deposits_net ?? 0) || null
      : sn.length ? sn[0].equity : null;
    const result = eqNow != null && eqStart != null ? eqNow - eqStart : null;
    let peak = -Infinity, dd = 0;
    for (const v of [...sn.map((s) => s.equity), ...(eqNow != null ? [eqNow] : [])]) {
      peak = Math.max(peak, v);
      dd = Math.max(dd, peak - v);
    }
    const qL = pos.filter((p) => p.side === "long").reduce((a, p) => a + Number(p.qty), 0);
    const qS = pos.filter((p) => p.side === "short").reduce((a, p) => a + Number(p.qty), 0);
    const swap = pos.reduce((a, p) => a + (Number(p.funding_usd) || 0), 0);
    const usedSum = Number(st?.used_margin) || 0;
    const used = qL + qS > 0 ? (usedSum * Math.max(qL, qS)) / (qL + qS) : 0;
    const eqTrue = st?.balance != null ? Number(st.balance) + Number(st.floating_pnl ?? 0) + swap : null;
    return {
      result,
      pct: result != null && eqStart ? (result / eqStart) * 100 : null,
      realized: tr.reduce((a, t) => a + (Number(t.pnl_usd) || 0), 0),
      cycles: tr.length,
      dd: sn.length ? dd : null,
      longs: pos.filter((p) => p.side === "long").length,
      shorts: pos.filter((p) => p.side === "short").length,
      floating: Number(st?.floating_pnl ?? 0),
      ml: eqTrue != null && used > 0 ? (eqTrue / used) * 100 : null,
    };
  };
  const L = side("live", live);
  const P = side("paper", paper);

  const since = new Date(start).toLocaleString("sk-SK",
    { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Bratislava" });

  type Row = [string, string, string, number | null, number | null, "hi" | "lo" | null, string?];
  const rows: Row[] = [
    ["Výsledok", signed(L.result, 0), signed(P.result, 0), L.result, P.result, "hi"],
    ["Výnos", L.pct == null ? "—" : `${signed(L.pct)} %`, P.pct == null ? "—" : `${signed(P.pct)} %`, L.pct, P.pct, "hi"],
    ["Realizované", signed(L.realized, 0), signed(P.realized, 0), L.realized, P.realized, "hi"],
    ["Cyklov", String(L.cycles), String(P.cycles), L.cycles, P.cycles, null],
    ["Max prepad", L.dd == null ? "—" : money(L.dd, 0), P.dd == null ? "—" : money(P.dd, 0), L.dd, P.dd, "lo"],
    ["Float teraz", signed(L.floating, 0), signed(P.floating, 0), L.floating, P.floating, null],
    ["Otvorené L/S", `${L.longs}/${L.shorts}`, `${P.longs}/${P.shorts}`, null, null, null],
    ["Marža teraz", L.ml == null ? "—" : `${L.ml.toFixed(0)} %`, P.ml == null ? "—" : `${P.ml.toFixed(0)} %`, L.ml, P.ml, "hi"],
  ];

  return (
    <main>
      <Section
        title="Súťaž"
        meta={`od ${since}`}
        info={
          <>
            Živý grid proti stratégii Strážca na virtuálnom účte. Strážca je ten
            istý grid, ale proti trendu (H1 EMA 100/400) drží najviac 4 pozície
            s dvojnásobným krokom, v smere trendu 20. Virtuálny účet verne
            simuluje cTrader na živých cenách: spread, TP medzi kotáciami,
            provízia 0,526 €, swap podľa sadzieb live účtu, marža len z väčšej
            strany a stop-out pri 50 %. Porovnáva sa zmena od štartu — live si
            nesie pozície spred súťaže, virtuálny účet začal prázdny s 15 200 €.
            Equity je zostatok + float, bez swapu otvorených pozícií (ten by live
            penalizoval za staré pozície); swap sa započíta, keď sa pozícia zavrie.
            Zelené je lepšie z dvojice.
          </>
        }
      >
        <div className="overflow-hidden rounded-lg bg-surface">
          <div className="grid grid-cols-[1fr_auto_auto] gap-x-4 px-3 py-2 text-xs text-faint">
            <span />
            <span className="w-24 text-right">{NAME.live}</span>
            <span className="w-24 text-right" style={{ color: COLOR.paper }}>{NAME.paper}</span>
          </div>
          {rows.map(([label, l, p, lv, pv, better]) => {
            const win = better && lv != null && pv != null && lv !== pv
              ? (better === "hi" ? (lv > pv ? "l" : "p") : (lv < pv ? "l" : "p")) : null;
            return (
              <div key={label}
                   className="grid grid-cols-[1fr_auto_auto] items-baseline gap-x-4 border-t border-hair px-3 py-2">
                <span className="text-sm text-muted">{label}</span>
                <span className={`num w-24 text-right ${win === "l" ? "text-pos font-semibold" : label === "Výsledok" ? pnlClass(lv) : ""}`}>{l}</span>
                <span className={`num w-24 text-right ${win === "p" ? "text-pos font-semibold" : label === "Výsledok" ? pnlClass(pv) : ""}`}>{p}</span>
              </div>
            );
          })}
        </div>
      </Section>

      <Section title="Zmena equity od štartu">
        <EquityChart snaps={snaps} live={live} paper={paper} />
      </Section>
    </main>
  );
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(358);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function EquityChart({ snaps, live, paper }: {
  snaps: Snap[]; live: BotState | null; paper: BotState | null;
}) {
  const [box, W] = useWidth();
  const H = 200, PL = 44, PR = 8, PT = 10, PB = 22;

  const series = useMemo(() => {
    const out: Record<Env, { t: number; v: number }[]> = { live: [], paper: [] };
    for (const env of ["live", "paper"] as Env[]) {
      const sn = snaps.filter((s) => s.env === env);
      if (!sn.length) continue;
      const base = env === "paper" ? Number(paper?.deposits_net ?? sn[0].equity) : sn[0].equity;
      // ~300 bodov stačí na šírku mobilu; staršie riedime rovnomerne
      const k = Math.max(1, Math.ceil(sn.length / 300));
      out[env] = sn.filter((_, i) => i % k === 0 || i === sn.length - 1)
        .map((s) => ({ t: Date.parse(s.ts), v: s.equity - base }));
    }
    return out;
  }, [snaps, paper]);

  const all = [...series.live, ...series.paper];
  if (all.length < 2) {
    return <Empty>Graf sa zaplní po prvých snapshotoch (každých 5 minút).</Empty>;
  }
  const t0 = Math.min(...all.map((p) => p.t)), t1 = Math.max(...all.map((p) => p.t));
  let lo = Math.min(0, ...all.map((p) => p.v)), hi = Math.max(0, ...all.map((p) => p.v));
  const pad = (hi - lo) * 0.08 || 50;
  lo -= pad; hi += pad;
  const x = (t: number) => PL + ((t - t0) / Math.max(1, t1 - t0)) * (W - PL - PR);
  const y = (v: number) => PT + ((hi - v) / (hi - lo)) * (H - PT - PB);
  const path = (pts: { t: number; v: number }[]) =>
    pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`).join(" ");

  // 3–4 popisky osi y na okrúhlych hodnotách
  const span = hi - lo;
  const step = [50, 100, 250, 500, 1000, 2500, 5000].find((s) => span / s <= 4) ?? 10_000;
  const ticks: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) ticks.push(v);
  const days: number[] = [];
  for (let d = new Date(t0); d.getTime() <= t1; d.setDate(d.getDate() + 1)) {
    const m = new Date(d); m.setHours(0, 0, 0, 0);
    if (m.getTime() > t0) days.push(m.getTime());
  }

  return (
    <div ref={box} className="w-full">
      <svg width={W} height={H} className="block" role="img"
           aria-label="Zmena equity od štartu súťaže: live grid a Strážca">
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PL} x2={W - PR} y1={y(v)} y2={y(v)}
                  stroke={v === 0 ? "var(--line)" : "var(--hair)"} />
            <text x={PL - 6} y={y(v) + 3.5} textAnchor="end" fontSize={10}
                  fill="var(--faint)" className="tabular-nums">{v === 0 ? "0" : signed(v, 0)}</text>
          </g>
        ))}
        {days.filter((_, i, a) => a.length < 10 || i % 2 === 0).map((d) => (
          <text key={d} x={x(d)} y={H - 6} textAnchor="middle" fontSize={10} fill="var(--faint)">
            {new Date(d).toLocaleDateString("sk-SK", { day: "numeric", month: "numeric" })}
          </text>
        ))}
        {(["live", "paper"] as Env[]).map((env) => series[env].length > 1 && (
          <path key={env} d={path(series[env])} fill="none" stroke={COLOR[env]}
                strokeWidth={1.75} strokeLinejoin="round" />
        ))}
      </svg>
      <ul className="mt-1 flex gap-4 text-[11px] text-muted">
        {(["live", "paper"] as Env[]).map((env) => {
          const last = series[env].at(-1);
          return (
            <li key={env} className="flex items-center gap-1.5">
              <span className="inline-block h-[3px] w-4 rounded-full" style={{ background: COLOR[env] }} />
              {NAME[env]}
              {last && <b className={`num ${pnlClass(last.v)}`}>{signed(last.v, 0)}</b>}
            </li>
          );
        })}
      </ul>
      {live == null && <p className="mt-1 text-xs text-faint">Live stav nedostupný.</p>}
    </div>
  );
}
