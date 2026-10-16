"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Empty, Section } from "@/components/ui";
import { money, pnlClass, signed } from "@/lib/format";
import type { BotState, Position } from "@/lib/types";

/**
 * Súťaž: živý grid proti všetkým virtuálnym účtom (paper_bot.py).
 *
 * Všetky účty sa porovnávajú v tom istom okne — od zvoleného štartu
 * (predvolene štart najmladšieho účtu, aby mali všetci rovnaké podmienky).
 * Výsledok = equity teraz − equity na začiatku okna (prvý 5-min snapshot
 * v okne). Equity = zostatok + float, bez naakumulovaného swapu otvorených
 * pozícií — ten by live penalizoval za pozície spred súťaže; swap sa do
 * výsledku dostane, keď sa pozícia zavrie. Live si nesie staré pozície,
 * virtuálne účty začali prázdne s 15 200 €.
 */

export type Snap = { env: string; ts: string; equity: number };
export type TradeRow = { env: string; pnl_usd: number | null; closed_at: string };
export type PosRow = Position & { env: string };
export type StateRow = BotState & { env: string };

// live neutrálne, virtuálne účty v poradí štartu
const PALETTE = ["#7c3aed", "#0891b2", "#d97706", "#db2777", "#65a30d", "#4f46e5"];

export type Account = { env: string; name: string; color: string; start: string | null };

export function accountsOf(states: StateRow[]): Account[] {
  const virt = states.filter((s) => s.env.startsWith("paper"))
    .map((s) => ({
      env: s.env,
      name: (s.config?.strategy_name as string) || s.env,
      start: (s.config?.started_at as string) ?? null,
    }))
    .sort((a, b) => (a.start ?? "").localeCompare(b.start ?? ""));
  const live = states.some((s) => s.env === "live")
    ? [{ env: "live", name: "Live grid", color: "var(--ink)", start: null }] : [];
  return [...live, ...virt.map((v, i) => ({ ...v, color: PALETTE[i % PALETTE.length] }))];
}

export default function Competition({ accounts, states, trades, positions, snaps, start, onStart }: {
  accounts: Account[]; states: StateRow[]; trades: TradeRow[]; positions: PosRow[];
  snaps: Snap[]; start: string | null; onStart: (s: string) => void;
}) {
  const virt = accounts.filter((a) => a.start);
  if (!virt.length || !start) {
    return (
      <main>
        <Section title="Súťaž">
          <Empty>Virtuálne účty ešte nebežia — porovnanie sa objaví po ich štarte.</Empty>
        </Section>
      </main>
    );
  }

  // okná porovnania = rôzne časy štartu (účty spustené naraz zdieľajú jedno)
  const starts = Object.entries(virt.reduce<Record<string, string[]>>((m, v) => {
    (m[v.start!] ??= []).push(v.name);
    return m;
  }, {})).sort(([a], [b]) => a.localeCompare(b));

  const fmt = (iso: string) => new Date(iso).toLocaleString("sk-SK",
    { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Bratislava" });

  const rows = accounts.map((acc) => {
    const st = states.find((s) => s.env === acc.env) ?? null;
    const pos = positions.filter((p) => p.env === acc.env);
    const tr = trades.filter((t) => t.env === acc.env);
    const sn = snaps.filter((s) => s.env === acc.env);
    const eqNow = st?.equity != null ? Number(st.equity) : null;
    // Účet, ktorý v okne ešte nemá snapshot, ale štartoval presne na jeho
    // začiatku, začína na vklade.
    const eqStart = sn.length ? sn[0].equity
      : acc.start && acc.start >= start ? Number(st?.deposits_net ?? 0) || null : null;
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
      acc,
      result,
      pct: result != null && eqStart ? (result / eqStart) * 100 : null,
      realized: tr.reduce((a, t) => a + (Number(t.pnl_usd) || 0), 0),
      cycles: tr.length,
      dd: sn.length ? dd : null,
      longs: pos.filter((p) => p.side === "long").length,
      shorts: pos.filter((p) => p.side === "short").length,
      ml: eqTrue != null && used > 0 ? (eqTrue / used) * 100 : null,
    };
  }).sort((a, b) => (b.result ?? -Infinity) - (a.result ?? -Infinity));

  return (
    <main>
      <Section
        title="Súťaž"
        meta={`od ${fmt(start)}`}
        info={
          <>
            Živý grid proti stratégiám na virtuálnych účtoch. Virtuálny účet verne
            simuluje cTrader na živých cenách: spread, TP medzi kotáciami, provízia
            0,526 €, swap podľa sadzieb live účtu, marža len z väčšej strany,
            stop-out pri 50 %. Všetci sa porovnávajú v tom istom okne — od štartu
            zvoleného účtu (predvolene najmladšieho). Výsledok je zmena equity
            v okne; equity je zostatok + float bez swapu otvorených pozícií (ten by
            live penalizoval za pozície spred súťaže). Live si nesie staré pozície,
            virtuálne účty začali prázdne s 15 200 €. Poradie podľa výsledku.
          </>
        }
      >
        {starts.length > 1 && (
          <div className="mb-3 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-faint">Porovnať od:</span>
            {starts.map(([t, names]) => (
              <button key={t} onClick={() => onStart(t)} aria-pressed={start === t}
                      title={names.join(", ")}
                      className={`rounded-full px-2.5 py-1 transition-colors ${
                        start === t ? "bg-ink text-bg" : "bg-surface text-muted hover:text-ink"}`}>
                {fmt(t)} · {names.length === 1 ? names[0] : `${names.length} účty`}
              </button>
            ))}
          </div>
        )}

        <ol className="overflow-hidden rounded-lg bg-surface">
          {rows.map((r, i) => (
            <li key={r.acc.env} className={`px-3 py-2.5 ${i ? "border-t border-hair" : ""}`}>
              <div className="flex items-baseline gap-2">
                <span className="w-4 text-xs text-faint tabular-nums">{i + 1}.</span>
                <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ background: r.acc.color }} />
                <span className="truncate text-sm font-medium">{r.acc.name}</span>
                <span className={`num ml-auto text-[15px] font-semibold ${pnlClass(r.result)}`}>
                  {r.result == null ? "—" : `${signed(r.result, 0)} €`}
                </span>
                <span className={`num w-16 text-right text-xs ${pnlClass(r.pct)}`}>
                  {r.pct == null ? "" : `${signed(r.pct)} %`}
                </span>
              </div>
              <div className="mt-0.5 pl-[42px] text-[11px] leading-relaxed text-faint">
                realiz. <span className={`num ${pnlClass(r.realized)}`}>{signed(r.realized, 0)}</span>
                {" · "}{r.cycles} cyklov
                {" · "}prepad <span className="num">{r.dd == null ? "—" : money(r.dd, 0)}</span>
                {" · "}marža <span className="num">{r.ml == null ? "—" : `${r.ml.toFixed(0)} %`}</span>
                {" · "}<span className="num">{r.longs}L/{r.shorts}S</span>
              </div>
            </li>
          ))}
        </ol>
      </Section>

      <Section title="Zmena equity v okne">
        <EquityChart accounts={accounts} snaps={snaps} states={states} start={start} />
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

function EquityChart({ accounts, snaps, states, start }: {
  accounts: Account[]; snaps: Snap[]; states: StateRow[]; start: string;
}) {
  const [box, W] = useWidth();
  const H = 210, PL = 44, PR = 8, PT = 10, PB = 22;

  const series = useMemo(() => {
    const out: Record<string, { t: number; v: number }[]> = {};
    for (const acc of accounts) {
      const sn = snaps.filter((s) => s.env === acc.env);
      if (!sn.length) continue;
      const st = states.find((s) => s.env === acc.env);
      const base = acc.start && acc.start >= start && st?.deposits_net
        ? Number(st.deposits_net) : sn[0].equity;
      const k = Math.max(1, Math.ceil(sn.length / 300));   // ~300 bodov stačí
      out[acc.env] = sn.filter((_, i) => i % k === 0 || i === sn.length - 1)
        .map((s) => ({ t: Date.parse(s.ts), v: s.equity - base }));
    }
    return out;
  }, [accounts, snaps, states, start]);

  const all = Object.values(series).flat();
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
           aria-label="Zmena equity všetkých účtov v okne súťaže">
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
        {accounts.map((acc) => (series[acc.env]?.length ?? 0) > 1 && (
          <path key={acc.env} d={path(series[acc.env])} fill="none" stroke={acc.color}
                strokeWidth={1.75} strokeLinejoin="round" />
        ))}
      </svg>
      <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
        {accounts.map((acc) => {
          const last = series[acc.env]?.at(-1);
          return (
            <li key={acc.env} className="flex items-center gap-1.5">
              <span className="inline-block h-[3px] w-4 rounded-full" style={{ background: acc.color }} />
              {acc.name}
              {last && <b className={`num ${pnlClass(last.v)}`}>{signed(last.v, 0)}</b>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
