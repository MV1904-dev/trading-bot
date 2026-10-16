"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { money } from "@/lib/format";
import { Empty, Tile } from "@/components/ui";
import {
  buildInput, futureLongs, scan, snapshotAt, thresholdPrice, type MapInput, type Snap,
} from "@/lib/riskmap";
import type { BotState, Position } from "@/lib/types";

/**
 * Rebrík pozícií a rizika na zvislej cenovej osi.
 *
 * Os ukazuje len to, čo je dôležité — od najvyššej otvorenej pozície (alebo
 * hornej hrany pásma) po stop-out — nie celé 1,00–1,30. Na celej osi mala
 * pozícia pol pixelu a všetko podstatné sa zlepilo do jedného pruhu.
 *
 * Graf sa kreslí v skutočných pixeloch (šírka z ResizeObserver), nie cez
 * viewBox: písmo a čiary tak majú na mobile aj na desktope rovnakú veľkosť
 * a výška je pevná.
 *
 * Stĺpce zľava: popis osi · longy · shorty · plocha „doliať".
 */

const H = 460, PAD_T = 14, PAD_B = 14;
const X_AXIS = 40, X_LONG = 48, X_SHORT = 80, BAR = 26, X_FUND = 116;

/** Šírka kontajnera v px; graf sa podľa nej prekreslí. */
function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(360);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

const pips = (a: number, b: number) => Math.round(Math.abs(a - b) * 10000);
/** Hranice sú z mriežky po 5 pipov — piate desatinné miesto by klamalo. */
const p4 = (v: number) => v.toFixed(4);

export default function RiskMap({
  state, positions,
}: { state: BotState | null; positions: Position[] }) {
  const [box, W] = useWidth();
  const [hover, setHover] = useState<Snap | null>(null);

  const model = useMemo(() => {
    if (!state) return null;
    const input = buildInput(state, positions);
    if (!input) return null;
    // Hranice hľadáme na širokej osi, zobrazujeme len výsek okolo nich.
    const wide = scan(input, 1.0, 1.3, 0.0005);
    const call = thresholdPrice(wide, 100);
    const stop = thresholdPrice(wide, 50);
    const plan = futureLongs(input, 1.0);
    const entries = input.legs.map((l) => l.entry);
    const lo = Math.min(stop ?? input.price - 0.03, ...entries, ...plan, input.price) - 0.003;
    const hi = Math.max(input.bandHigh, input.price, ...entries) + 0.003;
    const snaps = scan(input, lo, hi, 0.0002);
    return {
      input, snaps, call, stop, plan, lo, hi,
      maxFund: Math.max(...snaps.map((s) => s.fund100), 1),
    };
  }, [state, positions]);

  if (!model) {
    return <Empty>Čakám na kurz, zostatok a otvorené pozície z bota.</Empty>;
  }
  const { input, snaps, call, stop, plan, lo, hi, maxFund } = model;
  const px = input.price;
  const X_END = W - 6;

  const y = (p: number) => PAD_T + ((hi - p) / (hi - lo)) * (H - PAD_T - PAD_B);
  const priceAt = (yy: number) => hi - ((yy - PAD_T) / (H - PAD_T - PAD_B)) * (hi - lo);
  const fx = (v: number) => X_FUND + (v / maxFund) * (X_END - X_FUND);

  // Plocha „doliať" len tam, kde je čo doliať (pod margin callom).
  const need = snaps.filter((s) => s.fund100 > 0);
  const fundLine = need
    .map((s, n) => `${n ? "L" : "M"} ${fx(s.fund100).toFixed(1)} ${y(s.price).toFixed(1)}`)
    .join(" ");
  const fundArea = need.length
    ? `M ${X_FUND} ${y(need[0].price)} ${fundLine.replace(/^M/, "L")} ` +
      `L ${X_FUND} ${y(need[need.length - 1].price)} Z`
    : "";
  const fundAtStop = stop == null ? 0 : snapshotAt(input, stop).fund100;

  const ticks: number[] = [];
  for (let t = Math.ceil(lo * 100) / 100; t <= hi; t = Math.round((t + 0.01) * 100) / 100) {
    ticks.push(t);
  }

  const longs = input.legs.filter((l) => l.side === "long").length;
  const full = plan.length ? plan[plan.length - 1] : null;

  // Popisky hraníc vpravo; ak by sa zrazili s popiskom kurzu, odsunú sa.
  const marks = ([
    [call, "margin call", "var(--warn)"],
    [stop, "stop-out", "var(--neg)"],
  ] as [number | null, string, string][])
    .filter(([p]) => p != null) as [number, string, string][];

  const onMove = (clientY: number, el: SVGSVGElement) => {
    const r = el.getBoundingClientRect();
    const p = priceAt(clientY - r.top);
    if (p > hi || p < lo) return setHover(null);
    setHover(snapshotAt(input, Math.round(p * 10000) / 10000));
  };

  const bandTop = y(Math.min(input.bandHigh, hi));
  const bandBot = y(Math.max(input.bandLow, lo));

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 gap-2">
        <Tile label="Margin call" tone="text-warn"
              value={call == null ? "—" : p4(call)}
              sub={call == null ? "mimo 1,00–1,30" : `−${pips(px, call)} pipov`} />
        <Tile label="Stop-out" tone="text-neg"
              value={stop == null ? "—" : p4(stop)}
              sub={stop == null ? "mimo 1,00–1,30" : `−${pips(px, stop)} pipov`} />
        <Tile label="Kapacita" value={`${longs}/${input.cap}`}
              sub={full == null ? "plná" : `plná pri ${p4(full)}`} />
      </div>

      <div ref={box} className="relative w-full">
        <svg width={W} height={H} className="block touch-none select-none"
             role="img"
             aria-label="Otvorené pozície, ďalšie longy a hranice marže na cenovej osi"
             onPointerLeave={() => setHover(null)}
             onPointerDown={(e) => onMove(e.clientY, e.currentTarget)}
             onPointerMove={(e) => onMove(e.clientY, e.currentTarget)}>
          {/* pásmo — len podklad, nie značka */}
          {bandBot > bandTop && (
            <>
              <rect x={X_LONG - 4} y={bandTop} width={X_END - X_LONG + 4}
                    height={bandBot - bandTop} rx={6} fill="var(--surface)" />
              <text x={X_END - 4} y={bandTop + 13} textAnchor="end" fontSize={10}
                    fill="var(--faint)">pásmo</text>
            </>
          )}

          {/* mriežka po 100 pipov */}
          {ticks.map((t) => (
            <g key={t}>
              <line x1={X_LONG - 4} y1={y(t)} x2={X_END} y2={y(t)}
                    stroke="var(--hair)" strokeWidth={1} />
              <text x={X_AXIS - 6} y={y(t) + 3.5} textAnchor="end" fontSize={10}
                    fill="var(--faint)" className="tabular-nums">{t.toFixed(2)}</text>
            </g>
          ))}

          {/* doliať */}
          {fundArea && (
            <>
              <path d={fundArea} fill="var(--fund)" fillOpacity={0.16} />
              <path d={fundLine} fill="none" stroke="var(--fund)" strokeWidth={1.5}
                    strokeLinejoin="round" />
              {stop != null && fundAtStop > 0 && (
                <text x={X_FUND + 4} y={y(stop) + 14} fontSize={11} fontWeight={600}
                      fill="var(--fund)" className="tabular-nums">
                  doliať {money(fundAtStop, 0)} €
                </text>
              )}
            </>
          )}

          {/* hranice marže */}
          {marks.map(([p, label, col]) => (
            <g key={label}>
              <line x1={X_LONG - 4} y1={y(p)} x2={X_END} y2={y(p)}
                    stroke={col} strokeWidth={1} strokeDasharray="4 3" />
              <text x={X_FUND + 4} y={y(p) - 4} fontSize={10} fill={col}>{label}</text>
            </g>
          ))}

          {/* ďalšie longy, ktoré by grid cestou dole otvoril */}
          {plan.map((p) => (
            <rect key={`f${p}`} x={X_LONG + 0.5} y={y(p) - 1.5} width={BAR - 1}
                  height={3} rx={1.5} fill="none" stroke="var(--long)"
                  strokeOpacity={0.55} strokeWidth={1} />
          ))}

          {/* otvorené pozície */}
          {positions.map((p) => (
            <rect key={p.id} x={p.side === "long" ? X_LONG : X_SHORT}
                  y={y(p.entry_price) - 1.5} width={BAR} height={3} rx={1.5}
                  fill={p.side === "long" ? "var(--long)" : "var(--short)"} />
          ))}

          {/* aktuálny kurz */}
          <line x1={X_LONG - 4} y1={y(px)} x2={X_END} y2={y(px)}
                stroke="var(--ink)" strokeWidth={1.5} />
          <circle cx={X_LONG - 4} cy={y(px)} r={3.5} fill="var(--ink)" />
          <rect x={X_FUND} y={y(px) - 9} width={58} height={18} rx={9}
                fill="var(--ink)" />
          <text x={X_FUND + 29} y={y(px) + 3.5} textAnchor="middle" fontSize={11}
                fontWeight={600} fill="var(--bg)" className="tabular-nums">
            {px.toFixed(4)}
          </text>

          {/* zameriavač */}
          {hover && (
            <line x1={X_LONG - 4} y1={y(hover.price)} x2={X_END} y2={y(hover.price)}
                  stroke="var(--ink)" strokeWidth={1} strokeDasharray="2 3"
                  opacity={0.7} />
          )}
        </svg>

        {hover && <Bublina s={hover} top={y(hover.price)} />}
      </div>

      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
        <Znak c="var(--long)">long</Znak>
        <Znak c="var(--short)">short</Znak>
        <Znak c="var(--long)" hollow>ďalšie longy</Znak>
        <Znak c="var(--fund)" area>doliať na 100 %</Znak>
      </ul>

      <Tabulka input={input} />
    </div>
  );
}

function Znak({ c, hollow, area, children }: {
  c: string; hollow?: boolean; area?: boolean; children: React.ReactNode;
}) {
  return (
    <li className="flex items-center gap-1.5">
      <span className={area ? "h-2.5 w-3 rounded-sm" : "h-[3px] w-4 rounded-full"}
            style={hollow ? { outline: `1px solid ${c}`, opacity: 0.6 }
              : { background: c, opacity: area ? 0.5 : 1 }} />
      {children}
    </li>
  );
}

function Bublina({ s, top }: { s: Snap; top: number }) {
  const below = top < 70;
  return (
    <div className="pointer-events-none absolute right-1 z-10 rounded-lg bg-solid
                    px-2.5 py-1.5 text-xs shadow-lg ring-1 ring-hair"
         style={{ top, transform: below ? "translateY(10px)" : "translateY(calc(-100% - 10px))" }}>
      <div className="num text-[13px] font-semibold">{p4(s.price)}</div>
      <div className="text-faint">
        {s.longs} L / {s.shorts} S
        {s.level != null && <> · marža {s.level.toFixed(0)} %</>}
      </div>
      {s.fund100 > 0 && (
        <div className="font-semibold" style={{ color: "var(--fund)" }}>
          doliať {money(s.fund100, 0)} €
        </div>
      )}
    </div>
  );
}

/** Rovnaké čísla v texte — graf nesmie byť jediná cesta k údaju. */
function Tabulka({ input }: { input: MapInput }) {
  const rows = [1.1250, 1.1200, 1.1150, 1.1100, 1.1050, 1.1000, 1.0900, 1.0800]
    .map((p) => snapshotAt(input, p));
  return (
    <details className="group">
      <summary className="cursor-pointer list-none text-xs text-faint hover:text-muted">
        Tie isté čísla v tabuľke
        <span className="ml-1 inline-block transition-transform group-open:rotate-90">›</span>
      </summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-xs tabular-nums">
          <thead className="text-faint">
            <tr className="text-left">
              <th className="py-1 pr-3 font-normal">kurz</th>
              <th className="py-1 pr-3 font-normal">pozícií</th>
              <th className="py-1 pr-3 font-normal">marža</th>
              <th className="py-1 font-normal">doliať</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.price} className="border-t border-hair">
                <td className="py-1 pr-3">{s.price.toFixed(4)}</td>
                <td className="py-1 pr-3">{s.longs} L / {s.shorts} S</td>
                <td className="py-1 pr-3">
                  {s.level == null ? "—" : `${s.level.toFixed(0)} %`}
                </td>
                <td className="py-1">
                  {s.fund100 > 0 ? money(s.fund100, 0) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
