"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Empty, Section } from "@/components/ui";
import { pnlClass, signed } from "@/lib/format";
import { STRATEGY_INFO } from "@/lib/strategyInfo";
import type { BotState, Position } from "@/lib/types";

/**
 * Súťaž: živý grid proti všetkým virtuálnym účtom (paper_bot.py).
 *
 * Porovnáva sa KOHORTA — len pozície, ktoré daný účet otvoril od štartu
 * okna. Live si nesie pozície spred súťaže a ich zisk aj float by výsledok
 * skresľovali (zadanie 16. 10. 2026: „porovnanie od ďalšej pozície, ktorú
 * stratégia otvorí ako prvú"), takže sa do neho nepočítajú vôbec.
 *
 * Výsledok = čistý zisk uzavretých obchodov kohorty (po províziách a swape)
 * + float a naakumulovaný swap jej otvorených pozícií.
 */

export type TradeRow = { env: string; pnl_usd: number | null; opened_at: string; closed_at: string };
export type PosRow = Position & { env: string };
export type StateRow = BotState & { env: string };

// live neutrálne, virtuálne účty v poradí štartu
const PALETTE = ["#7c3aed", "#0891b2", "#d97706", "#db2777", "#65a30d", "#4f46e5"];

export type Account = { env: string; name: string; color: string; start: string | null; note?: string };

export function accountsOf(states: StateRow[]): Account[] {
  const virt = states.filter((s) => s.env.startsWith("paper"))
    .map((s) => ({
      env: s.env,
      name: (s.config?.strategy_name as string) || s.env,
      start: (s.config?.started_at as string) ?? null,
      note: (s.config?.strategy_note as string) || undefined,
    }))
    .sort((a, b) => (a.start ?? "").localeCompare(b.start ?? ""));
  const live = states.some((s) => s.env === "live")
    ? [{ env: "live", name: "Live grid", color: "var(--ink)", start: null }] : [];
  return [...live, ...virt.map((v, i) => ({ ...v, color: PALETTE[i % PALETTE.length] }))];
}

const ms = (iso: string) => Date.parse(iso);

export default function Competition({ accounts, trades, positions, start, onStart }: {
  accounts: Account[]; trades: TradeRow[]; positions: PosRow[];
  start: string | null; onStart: (s: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
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
  const t0 = ms(start);

  // okná porovnania = rôzne časy štartu (účty spustené naraz zdieľajú jedno)
  const starts = Object.entries(virt.reduce<Record<string, string[]>>((m, v) => {
    (m[v.start!] ??= []).push(v.name);
    return m;
  }, {})).sort(([a], [b]) => a.localeCompare(b));

  const fmt = (iso: string) => new Date(iso).toLocaleString("sk-SK",
    { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Bratislava" });

  const rows = accounts.map((acc) => {
    const tr = trades.filter((t) => t.env === acc.env && ms(t.opened_at) >= t0);
    const pos = positions.filter((p) => p.env === acc.env && ms(p.opened_at) >= t0);
    const realized = tr.reduce((a, t) => a + (Number(t.pnl_usd) || 0), 0);
    const floating = pos.reduce((a, p) => a + (Number(p.pnl_float) || 0), 0);
    const swap = pos.reduce((a, p) => a + (Number(p.funding_usd) || 0), 0);
    const first = [...tr.map((t) => t.opened_at), ...pos.map((p) => p.opened_at)].sort()[0] ?? null;
    return {
      acc, realized, floating: floating + swap, first,
      result: realized + floating + swap,
      cycles: tr.length,
      losses: tr.filter((t) => (Number(t.pnl_usd) || 0) < 0).length,
      longs: pos.filter((p) => p.side === "long").length,
      shorts: pos.filter((p) => p.side === "short").length,
    };
  }).sort((a, b) => b.result - a.result);

  return (
    <main>
      <Section
        title="Súťaž"
        meta={`od ${fmt(start)}`}
        info={
          <>
            Živý grid proti stratégiám na virtuálnych účtoch. Každý účet sa hodnotí
            len podľa pozícií, ktoré otvoril od štartu okna — staré pozície live
            (spred súťaže) sa nepočítajú vôbec, ani ich zisk, ani float. Výsledok je
            čistý zisk uzavretých obchodov (po províziách a swape) plus float
            a swap pozícií, ktoré sú z nich ešte otvorené. Virtuálny účet verne
            simuluje cTrader na živých cenách: spread, TP medzi kotáciami, provízia
            0,526 €, swap podľa sadzieb live účtu, marža len z väčšej strany,
            stop-out pri 50 %. Okno sa dá posunúť na štart iného účtu. Poradie
            podľa výsledku. Ťuknutím na účet sa ukážu pravidlá jeho stratégie — čo bot kedy urobí a prečo.
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
          {rows.map((r, i) => {
            const info = STRATEGY_INFO[r.acc.env];
            const isOpen = open === r.acc.env;
            return (
            <li key={r.acc.env} className={i ? "border-t border-hair" : ""}>
              <button onClick={() => setOpen(isOpen ? null : r.acc.env)} aria-expanded={isOpen}
                      className="block w-full px-3 py-2.5 text-left hover:bg-hair/40">
              <div className="flex items-baseline gap-2">
                <span className="w-4 text-xs text-faint tabular-nums">{i + 1}.</span>
                <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ background: r.acc.color }} />
                <span className="truncate text-sm font-medium">{r.acc.name}</span>
                <span className={`num ml-auto text-[15px] font-semibold ${pnlClass(r.result)}`}>
                  {r.first ? `${signed(r.result, 0)} €` : "—"}
                </span>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                     className={`shrink-0 self-center text-faint transition-transform ${isOpen ? "rotate-180" : ""}`}>
                  <path d="m6 9 6 6 6-6" />
                </svg>
              </div>
              <div className="mt-0.5 pl-[42px] text-[11px] leading-relaxed text-faint">
                {r.first ? (
                  <>
                    realiz. <span className={`num ${pnlClass(r.realized)}`}>{signed(r.realized, 0)}</span>
                    {" · "}float <span className={`num ${pnlClass(r.floating)}`}>{signed(r.floating, 0)}</span>
                    {" · "}{r.cycles} cyklov{r.losses ? ` (${r.losses} so stratou)` : ""}
                    {" · "}otvorené <span className="num">{r.longs}L/{r.shorts}S</span>
                    {" · "}prvý vstup {fmt(r.first)}
                  </>
                ) : "čaká na prvý vstup"}
              </div>
              </button>
              {isOpen && (
                <div className="mx-3 mb-3 rounded-lg bg-bg px-3 py-2.5 text-[13px] leading-relaxed">
                  {info ? (
                    <>
                      <p className="text-ink">{info.summary}</p>
                      <ol className="mt-2 space-y-2">
                        {info.rules.map((rule, k) => (
                          <li key={k} className="rounded-md border border-hair px-2.5 py-2">
                            <p className="text-xs font-semibold text-ink">
                              <span className="mr-1 text-faint tabular-nums">{k + 1}.</span>
                              Kedy: <span className="font-normal">{rule.when}</span>
                            </p>
                            <p className="mt-1 text-xs text-ink">
                              <span className="font-semibold">Čo urobí:</span> {rule.what}
                            </p>
                            <p className="mt-1 text-xs text-muted">
                              <span className="font-semibold">Prečo:</span> {rule.why}
                            </p>
                          </li>
                        ))}
                      </ol>
                      <p className="mt-2 text-xs text-muted">
                        <span className="font-semibold text-warn">Riziko:</span> {info.risk}
                      </p>
                    </>
                  ) : (
                    <p className="text-muted">{r.acc.note ?? "Popis tejto stratégie zatiaľ chýba."}</p>
                  )}
                </div>
              )}
            </li>
            );
          })}
        </ol>
      </Section>

      <Section title="Realizovaný zisk od štartu">
        <RealizedChart accounts={accounts} trades={trades} t0={t0} />
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

/** Kumulatívny čistý zisk uzavretých obchodov kohorty — schodíky po
    každom zavretí. Float sa nekreslí: jeho históriu pre live nemáme. */
function RealizedChart({ accounts, trades, t0 }: {
  accounts: Account[]; trades: TradeRow[]; t0: number;
}) {
  const [box, W] = useWidth();
  const H = 200, PL = 44, PR = 8, PT = 10, PB = 22;
  const [now] = useState(() => Date.now());

  const series = useMemo(() => {
    const out: Record<string, { t: number; v: number }[]> = {};
    for (const acc of accounts) {
      const tr = trades.filter((t) => t.env === acc.env && ms(t.opened_at) >= t0)
        .sort((a, b) => a.closed_at.localeCompare(b.closed_at));
      let cum = 0;
      const pts = [{ t: t0, v: 0 }];
      for (const t of tr) {
        const at = ms(t.closed_at);
        pts.push({ t: at, v: cum });
        cum += Number(t.pnl_usd) || 0;
        pts.push({ t: at, v: cum });
      }
      pts.push({ t: Math.max(now, pts[pts.length - 1].t), v: cum });
      out[acc.env] = pts;
    }
    return out;
  }, [accounts, trades, t0, now]);

  const all = Object.values(series).flat();
  if (!trades.some((t) => ms(t.opened_at) >= t0)) {
    return <Empty>Graf sa zaplní po prvých uzavretých obchodoch.</Empty>;
  }
  const t1 = Math.max(...all.map((p) => p.t));
  let lo = Math.min(0, ...all.map((p) => p.v)), hi = Math.max(0, ...all.map((p) => p.v));
  const pad = (hi - lo) * 0.08 || 20;
  lo -= pad; hi += pad;
  const x = (t: number) => PL + ((t - t0) / Math.max(1, t1 - t0)) * (W - PL - PR);
  const y = (v: number) => PT + ((hi - v) / (hi - lo)) * (H - PT - PB);
  const path = (pts: { t: number; v: number }[]) =>
    pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`).join(" ");

  const span = hi - lo;
  const step = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000].find((s) => span / s <= 4) ?? 10_000;
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
           aria-label="Kumulatívny realizovaný zisk účtov od štartu súťaže">
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
        {accounts.map((acc) => (
          <path key={acc.env} d={path(series[acc.env] ?? [])} fill="none" stroke={acc.color}
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
