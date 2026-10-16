"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { money } from "@/lib/format";
import { Empty, Tile } from "@/components/ui";
import {
  buildInput, futureLongs, futureShorts, scan, snapshotAt, thresholdPrice, type MapInput, type Snap,
} from "@/lib/riskmap";
import type { BotState, Position } from "@/lib/types";

/**
 * Mapa rizika (zadanie 16. 10. 2026, varianta A + C), oboma smermi —
 * pri páde pribúdajú longy, pri raste shorty a každý smer má vlastný
 * margin call a stop-out:
 *  - vodorovný pás pozícií — rovnaký jazyk ako graf na Prehľade: cena
 *    zľava doprava, longy nad osou, shorty pod ňou, vybledlé čiarky sú
 *    longy, ktoré grid ešte otvorí; zvislé čiary margin call a stop-out,
 *  - pod ním scenár — riadok po 50 pipoch od kurzu hore aj dole s počtom
 *    pozícií, úrovňou marže a sumou na doliatie. Čísla sa čítajú priamo,
 *    nie odhadom z plochy grafu.
 *
 * Os pásu ide od stop-outu po najvyššiu pozíciu, nie 1,00–1,30: na celej
 * osi mala pozícia pol pixelu. Kreslí sa v skutočných pixeloch (šírka
 * z ResizeObserver), takže písmo má na mobile aj desktope rovnakú veľkosť.
 */

const H = 132, AX = 74, TICK = 24, PAD = 8;

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

const pips = (a: number, b: number) => Math.round(Math.abs(a - b) * 10000);
/** Hranice sú z mriežky po 5 pipov — piate desatinné miesto by klamalo. */
const p4 = (v: number) => v.toFixed(4);

export default function RiskMap({
  state, positions,
}: { state: BotState | null; positions: Position[] }) {
  const model = useMemo(() => {
    if (!state) return null;
    const input = buildInput(state, positions);
    if (!input) return null;
    const wide = scan(input, 1.0, 1.3, 0.0005);
    const px = input.price;
    return {
      input,
      lim: {
        callDn: thresholdPrice(wide, 100, px, "down"),
        stopDn: thresholdPrice(wide, 50, px, "down"),
        callUp: thresholdPrice(wide, 100, px, "up"),
        stopUp: thresholdPrice(wide, 50, px, "up"),
      },
      planL: futureLongs(input, 1.0),
      planS: futureShorts(input, 1.3),
    };
  }, [state, positions]);

  if (!model) {
    return <Empty>Čakám na kurz, zostatok a otvorené pozície z bota.</Empty>;
  }
  const { input, lim, planL, planS } = model;
  const px = input.price;
  const longs = input.legs.filter((l) => l.side === "long").length;
  const shorts = input.legs.length - longs;
  const fullL = planL.length ? planL[planL.length - 1] : null;
  const fullS = planS.length ? planS[planS.length - 1] : null;
  const dist = (p: number | null) =>
    p == null ? "mimo 1,00–1,30" : `${p > px ? "+" : "−"}${pips(px, p)} pipov`;

  // Horný riadok rast (shorty), dolný pád (longy) — rovnako ako scenár
  // pod pásom, kde sú vyššie kurzy hore.
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 gap-2">
        <Tile label="Margin call ↑" tone="text-warn"
              value={lim.callUp == null ? "—" : p4(lim.callUp)} sub={dist(lim.callUp)} />
        <Tile label="Stop-out ↑" tone="text-neg"
              value={lim.stopUp == null ? "—" : p4(lim.stopUp)} sub={dist(lim.stopUp)} />
        <Tile label="Shorty" value={`${shorts}/${input.cap}`}
              sub={fullS == null ? "plné" : `plné pri ${p4(fullS)}`} />
        <Tile label="Margin call ↓" tone="text-warn"
              value={lim.callDn == null ? "—" : p4(lim.callDn)} sub={dist(lim.callDn)} />
        <Tile label="Stop-out ↓" tone="text-neg"
              value={lim.stopDn == null ? "—" : p4(lim.stopDn)} sub={dist(lim.stopDn)} />
        <Tile label="Longy" value={`${longs}/${input.cap}`}
              sub={fullL == null ? "plné" : `plné pri ${p4(fullL)}`} />
      </div>

      <Pas input={input} positions={positions} lim={lim} planL={planL} planS={planS} />
      <Scenar input={input} lim={lim} />
    </div>
  );
}

type Lim = { callDn: number | null; stopDn: number | null;
             callUp: number | null; stopUp: number | null };

/** Vodorovný pás: kde ležia pozície a hranice marže. */
function Pas({ input, positions, lim, planL, planS }: {
  input: MapInput; positions: Position[]; lim: Lim; planL: number[]; planS: number[];
}) {
  const [box, W] = useWidth();
  const [hover, setHover] = useState<Snap | null>(null);
  const px = input.price;

  const entries = input.legs.map((l) => l.entry);
  const lo = Math.min(lim.stopDn ?? px - 0.03, ...entries, ...planL, px) - 0.002;
  const hi = Math.max(lim.stopUp ?? px + 0.03, ...entries, ...planS, px) + 0.002;
  const x = (p: number) => PAD + ((p - lo) / (hi - lo)) * (W - 2 * PAD);
  const priceAt = (xx: number) => lo + ((xx - PAD) / (W - 2 * PAD)) * (hi - lo);

  const every = hi - lo > 0.12 ? 0.02 : 0.01;
  const ticks: number[] = [];
  for (let t = Math.ceil(lo / every) * every; t <= hi; t += every) ticks.push(Number(t.toFixed(2)));

  const bandL = x(Math.max(input.bandLow, lo));
  const bandR = x(Math.min(input.bandHigh, hi));
  const pill = Math.min(Math.max(x(px), PAD + 28), W - PAD - 28);

  // Hranice bez popiskov v páse: pri oboch smeroch sú štyri a popisky sa
  // zrážali s kurzom a pozíciami. Farba čiary + legenda, čísla v dlaždiciach.
  const marks = ([
    [lim.stopDn, "var(--neg)"], [lim.callDn, "var(--warn)"],
    [lim.callUp, "var(--warn)"], [lim.stopUp, "var(--neg)"],
  ] as [number | null, string][])
    .filter(([p]) => p != null)
    .map(([p, col]) => ({ p: p!, col }));

  const onMove = (clientX: number, el: SVGSVGElement) => {
    const p = priceAt(clientX - el.getBoundingClientRect().left);
    if (p < lo || p > hi) return setHover(null);
    setHover(snapshotAt(input, Math.round(p * 10000) / 10000));
  };

  return (
    <div ref={box} className="w-full">
      <svg width={W} height={H} className="block touch-pan-y select-none"
           role="img" aria-label="Otvorené pozície, ďalšie longy a hranice marže na cenovej osi"
           onPointerLeave={() => setHover(null)}
           onPointerDown={(e) => onMove(e.clientX, e.currentTarget)}
           onPointerMove={(e) => onMove(e.clientX, e.currentTarget)}>
        {bandR > bandL && (
          <>
            <rect x={bandL} y={AX - TICK - 8} width={bandR - bandL} height={2 * TICK + 16}
                  rx={6} fill="var(--surface)" />
          </>
        )}

        <line x1={PAD} y1={AX} x2={W - PAD} y2={AX} stroke="var(--line)" />
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} y1={AX - 3} x2={x(t)} y2={AX + 3} stroke="var(--line)" />
            <text x={x(t)} y={H - 4} textAnchor="middle" fontSize={10}
                  fill="var(--faint)" className="tabular-nums">{t.toFixed(2)}</text>
          </g>
        ))}

        {marks.map((m) => (
          <line key={m.p} x1={x(m.p)} y1={AX - TICK - 14} x2={x(m.p)} y2={AX + TICK + 8}
                stroke={m.col} strokeWidth={1.5} strokeDasharray="3 3" />
        ))}

        {planL.map((p) => (
          <line key={`fl${p}`} x1={x(p)} y1={AX - TICK - 4} x2={x(p)} y2={AX - 5}
                stroke="var(--long)" strokeWidth={2} strokeOpacity={0.35}
                strokeDasharray="3 2" />
        ))}
        {planS.map((p) => (
          <line key={`fs${p}`} x1={x(p)} y1={AX + 5} x2={x(p)} y2={AX + TICK + 4}
                stroke="var(--short)" strokeWidth={2} strokeOpacity={0.35}
                strokeDasharray="3 2" />
        ))}
        {positions.map((p) => {
          const up = p.side === "long";
          return (
            <line key={p.id} x1={x(p.entry_price)} x2={x(p.entry_price)}
                  y1={up ? AX - TICK - 4 : AX + 5} y2={up ? AX - 5 : AX + TICK + 4}
                  stroke={up ? "var(--long)" : "var(--short)"} strokeWidth={2}
                  strokeLinecap="round" />
          );
        })}

        <line x1={x(px)} y1={AX - TICK - 20} x2={x(px)} y2={AX + TICK + 8}
              stroke="var(--ink)" strokeWidth={1.5} />
        <rect x={pill - 28} y={2} width={56} height={18} rx={9} fill="var(--ink)" />
        <text x={pill} y={15} textAnchor="middle" fontSize={11} fontWeight={600}
              fill="var(--bg)" className="tabular-nums">{p4(px)}</text>

        {hover && (
          <line x1={x(hover.price)} y1={AX - TICK - 8} x2={x(hover.price)} y2={AX + TICK + 8}
                stroke="var(--ink)" strokeDasharray="2 3" opacity={0.6} />
        )}
      </svg>

      {/* Odčítanie pod pásom, nie bublina v ňom — pás je nízky a bublina
          by prekryla práve tie pozície, na ktoré sa ukazuje. */}
      <p className="mt-1 h-4 text-[11px] tabular-nums text-muted">
        {hover ? (
          <>
            <b className="text-ink">{p4(hover.price)}</b>
            {" · "}{hover.longs} L / {hover.shorts} S
            {hover.level != null && ` · marža ${hover.level.toFixed(0)} %`}
            {hover.fund100 > 0 && (
              <b style={{ color: "var(--fund)" }}> · doliať {money(hover.fund100, 0)} €</b>
            )}
          </>
        ) : (
          <span className="text-faint">
            <Znak c="var(--long)" /> long · <Znak c="var(--short)" /> short ·{" "}
            <Znak c="var(--long)" faded /><Znak c="var(--short)" faded /> ďalšie ·{" "}
            <Ciara c="var(--warn)" /> margin call · <Ciara c="var(--neg)" /> stop-out
          </span>
        )}
      </p>
    </div>
  );
}

function Znak({ c, faded }: { c: string; faded?: boolean }) {
  return (
    <span className="mx-0.5 inline-block h-2.5 w-[3px] rounded-full align-[-1px]"
          style={{ background: c, opacity: faded ? 0.35 : 1 }} />
  );
}

function Ciara({ c }: { c: string }) {
  return (
    <span className="mx-0.5 inline-block h-2.5 align-[-1px]"
          style={{ borderLeft: `1.5px dashed ${c}` }} />
  );
}

/** Scenár: riadok po 50 pipoch od kurzu hore aj dole, hranice vsunuté. */
function Scenar({ input, lim }: { input: MapInput; lim: Lim }) {
  const px = input.price;
  const floor = (lim.stopDn ?? px - 0.03) - 0.001;
  const ceil = (lim.stopUp ?? px + 0.03) + 0.001;
  type Row = { p: number; kind?: "now" | "call" | "stop" };
  const rows: Row[] = [{ p: px, kind: "now" }];
  for (let n = Math.ceil(floor / 0.005); n * 0.005 <= ceil; n++) {
    const p = Number((n * 0.005).toFixed(4));
    if (Math.abs(p - px) > 0.00005) rows.push({ p });
  }
  for (const [p, kind] of [[lim.callDn, "call"], [lim.stopDn, "stop"],
                           [lim.callUp, "call"], [lim.stopUp, "stop"]] as const) {
    if (p != null) rows.push({ p, kind });
  }
  rows.sort((a, b) => b.p - a.p);
  // Riadok mriežky, ktorý padne presne na hranicu, sa nezdvojí.
  const uniq = rows.filter((r, n) => !(r.kind == null && rows.some(
    (o, m) => m !== n && o.kind != null && Math.abs(o.p - r.p) < 0.00005)));

  const tone = (lv: number) =>
    lv < 50 ? "var(--neg)" : lv < 200 ? "var(--warn)" : "var(--pos)";

  return (
    <div>
      <div className="mb-1 flex text-[11px] text-faint">
        <span className="w-[62px] pl-1.5">kurz</span>
        <span className="w-[74px]" />
        <span className="w-[50px]">pozície</span>
        <span className="flex-1">marža</span>
        <span className="w-[62px] pr-1.5 text-right">doliať</span>
      </div>
      {uniq.map((r) => {
        const s = snapshotAt(input, r.p);
        const lv = s.level ?? 0;
        const bg = r.kind === "now" ? "bg-surface"
          : r.kind === "call" ? "bg-warn/10" : r.kind === "stop" ? "bg-neg/10" : "";
        return (
          <div key={`${r.kind ?? ""}${r.p}`}
               className={`flex items-center border-t border-hair py-1.5 text-[12.5px] ${bg}`}>
            <span className="w-[62px] pl-1.5 tabular-nums">{p4(r.p)}</span>
            <span className="w-[74px] truncate text-[11px] text-muted">
              {r.kind === "now" ? <b className="text-ink">teraz</b>
                : r.kind === "call" ? <b className="text-warn">margin call</b>
                : r.kind === "stop" ? <b className="text-neg">stop-out</b>
                : <span className="tabular-nums">{r.p > px ? "+" : "−"}{pips(px, r.p)} p</span>}
            </span>
            <span className="w-[50px] text-[11.5px] tabular-nums text-muted">
              {s.longs}L {s.shorts}S
            </span>
            <span className="flex flex-1 items-center gap-1.5">
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-hair">
                <span className="block h-full rounded-full"
                      style={{ width: `${Math.min(lv, 300) / 3}%`, background: tone(lv),
                               opacity: lv >= 100 && lv < 200 ? 0.6 : 1 }} />
              </span>
              <span className="w-[38px] text-right text-[11.5px] tabular-nums">
                {s.level == null ? "—" : `${lv.toFixed(0)} %`}
              </span>
            </span>
            <span className="w-[62px] pr-1.5 text-right tabular-nums"
                  style={{ color: s.fund100 > 0 ? "var(--fund)" : "var(--faint)",
                           fontWeight: s.fund100 > 0 ? 600 : 400 }}>
              {s.fund100 > 0 ? `${money(s.fund100, 0)} €` : "—"}
            </span>
          </div>
        );
      })}
    </div>
  );
}
