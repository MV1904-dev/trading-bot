"use client";

import { useMemo, useState } from "react";
import { money, price as fmtPrice } from "@/lib/format";
import { Empty } from "@/components/ui";
import { buildInput, scan, snapshotAt, thresholdPrice, type Snap } from "@/lib/riskmap";
import type { BotState, Position } from "@/lib/types";

/**
 * Celá cenová os naraz: kde bot otvára longy, kde shorty a koľko peňazí si
 * pýta, aby ho margin call nezavrel.
 *
 * Os je zvislá zámerne. Vodorovne by sa 3 000 pipov na mobile zmestilo do
 * ~370 px a celé pásmo by malo 50 px; zvislo má stránka výšky koľko treba
 * a cena sa číta ako rebrík, čo je aj mentálny model obchodníka.
 *
 * Tri značky = tri úlohy: dva pásy sú identita (ktorá strana sa otvára),
 * plocha vpravo je veľkosť (koľko doliať). Preto NIE sú na jednej osi
 * zlúčené — zdieľajú len cenovú os, každá má vlastný stĺpec.
 */

const TOP = 1.30, BOTTOM = 1.00;
const W = 340, H = 700, PAD_T = 18, PAD_B = 22;
const X_LONG = 50, X_SHORT = 74, COL = 18;      // 6 px medzera medzi pásmi
const X_TICK = 100, X_FUND = 120, X_MAX = 330;

const y = (p: number) =>
  PAD_T + ((TOP - p) / (TOP - BOTTOM)) * (H - PAD_T - PAD_B);
const priceAt = (yy: number) =>
  TOP - ((yy - PAD_T) / (H - PAD_T - PAD_B)) * (TOP - BOTTOM);

export default function RiskMap({
  state, positions,
}: { state: BotState | null; positions: Position[] }) {
  const [hover, setHover] = useState<Snap | null>(null);

  const model = useMemo(() => {
    if (!state) return null;
    const input = buildInput(state, positions);
    if (!input) return null;
    const snaps = scan(input, BOTTOM, TOP, 0.0005);
    return {
      input, snaps,
      call: thresholdPrice(snaps, 100),
      stop: thresholdPrice(snaps, 50),
      maxFund: Math.max(...snaps.map((s) => s.fund100), 1),
    };
  }, [state, positions]);

  if (!model) {
    return <Empty>Čakám na kurz, zostatok a otvorené pozície z bota.</Empty>;
  }
  const { input, snaps, call, stop, maxFund } = model;
  const px = input.price;
  const fx = (v: number) => X_FUND + (v / maxFund) * (X_MAX - X_FUND);

  // Plocha potrebného vkladu. `snaps` idú od najnižšej ceny nahor, takže
  // polygón musí ísť rovnako — pri opačnom poradí vrcholov sa z neho stane
  // presýpacie hodiny cez celú os (a vyzerá to, že vklad treba aj hore).
  const line = snaps
    .map((s, n) => `${n ? "L" : "M"} ${fx(s.fund100).toFixed(1)} ${y(s.price).toFixed(1)}`)
    .join(" ");
  const area = `M ${X_FUND} ${y(BOTTOM)} ${line.slice(1)} L ${X_FUND} ${y(TOP)} Z`;

  // Súvislé úseky, kde sa daná strana otvára — jeden obdĺžnik na úsek,
  // nie 600 prúžkov za sebou.
  const spans = (key: "opensLong" | "opensShort") => {
    const out: [number, number][] = [];
    let start: number | null = null;
    for (const s of snaps) {
      if (s[key] && start == null) start = s.price;
      if (!s[key] && start != null) { out.push([start, s.price]); start = null; }
    }
    if (start != null) out.push([start, TOP]);
    return out;
  };

  const now = snapshotAt(input, px);
  const shown = hover ?? now;
  const ticks = [1.30, 1.25, 1.20, 1.15, 1.10, 1.05, 1.00];

  return (
    <div className="flex flex-col gap-3">
      <Hladiny px={px} call={call} stop={stop} input={input} />
      <Legenda />

      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full"
             role="img"
             aria-label="Cenová os 1,00 až 1,30: kde sa otvárajú longy a shorty a koľko treba doliať"
             onMouseLeave={() => setHover(null)}
             onMouseMove={(e) => {
               const r = e.currentTarget.getBoundingClientRect();
               const p = priceAt(((e.clientY - r.top) / r.height) * H);
               if (p > TOP || p < BOTTOM) return setHover(null);
               setHover(snapshotAt(input, Math.round(p * 2000) / 2000));
             }}>
          {/* mriežka a popis osi */}
          {ticks.map((t) => (
            <g key={t}>
              <line x1={X_LONG} y1={y(t)} x2={X_MAX} y2={y(t)}
                    stroke="var(--hair)" strokeWidth={1} />
              <text x={44} y={y(t) + 4} textAnchor="end" fontSize={11}
                    fill="var(--faint)" className="tabular-nums">
                {t.toFixed(2)}
              </text>
            </g>
          ))}

          {/* pásy: kde sa strana otvára */}
          {spans("opensLong").map(([a, b]) => (
            <rect key={`l${a}`} x={X_LONG} y={y(b)} width={COL}
                  height={Math.max(1, y(a) - y(b))} rx={3}
                  fill="var(--long)" fillOpacity={0.9} />
          ))}
          {spans("opensShort").map(([a, b]) => (
            <rect key={`s${a}`} x={X_SHORT} y={y(b)} width={COL}
                  height={Math.max(1, y(a) - y(b))} rx={3}
                  fill="var(--short)" fillOpacity={0.9} />
          ))}
          {/* Pásy sa nepopisujú v grafe: pri 18 px stĺpcoch sa „long" a „short"
              prekrývali do „longshort". Identitu nesie legenda nad grafom. */}

          {/* otvorené pozície */}
          {positions.map((p) => (
            <line key={p.id} x1={X_TICK} y1={y(p.entry_price)}
                  x2={X_TICK + 12} y2={y(p.entry_price)}
                  stroke={p.side === "long" ? "var(--long)" : "var(--short)"}
                  strokeWidth={2} strokeLinecap="round" />
          ))}

          {/* potrebný vklad */}
          <path d={area} fill="var(--fund)" fillOpacity={0.22} />
          <path d={line} fill="none" stroke="var(--fund)"
                strokeWidth={2} strokeLinejoin="round" />
          <line x1={X_FUND} y1={PAD_T} x2={X_FUND} y2={H - PAD_B}
                stroke="var(--line)" strokeWidth={1} />

          {/* Hranice, pri ktorých to praská. Bez popiskov v grafe: ležia
              46 a 176 pipov od kurzu, čo je na osi cez 3 000 pipov 10 a 39 px —
              tri popisky by sa zlepili na seba. Čísla sú v dlaždiciach hore. */}
          {([[call, "var(--warn)"], [stop, "var(--neg)"]] as [number | null, string][])
            .filter(([p]) => p != null)
            .map(([p, col]) => (
              <line key={col} x1={X_LONG} y1={y(p!)} x2={X_MAX} y2={y(p!)}
                    stroke={col} strokeWidth={2} strokeDasharray="5 4" />
            ))}

          {/* aktuálny kurz */}
          <line x1={X_LONG - 6} y1={y(px)} x2={X_MAX} y2={y(px)}
                stroke="var(--ink)" strokeWidth={2.5} />
          <circle cx={X_LONG - 6} cy={y(px)} r={4.5} fill="var(--ink)"
                  stroke="var(--bg)" strokeWidth={2} />
          <text x={X_MAX} y={y(px) - 6} textAnchor="end" fontSize={12}
                fontWeight={600} fill="var(--ink)" className="tabular-nums">
            teraz {fmtPrice(px)}
          </text>

          {/* zameriavač */}
          {hover && (
            <line x1={X_LONG} y1={y(hover.price)} x2={X_MAX} y2={y(hover.price)}
                  stroke="var(--ink)" strokeWidth={1} strokeDasharray="2 3"
                  opacity={0.6} />
          )}
        </svg>

        {/* Len pri ukázaní myšou: bez toho sedel natrvalo na čiare
            aktuálneho kurzu a prekrýval jej vlastný popisok. */}
        {hover && (
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute right-0 w-fit rounded-md border border-hair
                          bg-solid px-2 py-1 text-[11px] shadow-sm"
               style={{ top: `${(y(shown.price) / H) * 100}%`,
                        transform: "translateY(-130%)" }}>
            <b className="tabular-nums">{fmtPrice(shown.price)}</b>{" "}
            {shown.opensLong && shown.opensShort ? "long + short"
              : shown.opensLong ? "len longy"
              : shown.opensShort ? "len shorty" : "nič sa neotvára"}
            <span className="text-faint">
              {" · "}{shown.longs}L/{shown.shorts}S
              {shown.level != null && ` · ${shown.level.toFixed(0)} %`}
            </span>
            {shown.fund100 > 0 && (
              <b className="ml-1" style={{ color: "var(--fund)" }}>
                doliať {money(shown.fund100, 0)}
              </b>
            )}
          </div>
        </div>
        )}
      </div>

      <Tabulka input={input} />
    </div>
  );
}

function Legenda() {
  const items: [string, string, string][] = [
    ["var(--long)", "otvára longy", "pod hornou hranou pásma"],
    ["var(--short)", "otvára shorty", "nad dolnou hranou pásma"],
    ["var(--fund)", "treba doliať", "aby marža neklesla pod 100 %"],
  ];
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
      {items.map(([c, t, note]) => (
        <li key={t} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: c }} />
          <span className="text-ink">{t}</span>
          <span className="text-faint">· {note}</span>
        </li>
      ))}
    </ul>
  );
}

/** Rovnaké čísla v texte — graf nesmie byť jediná cesta k údaju. */
function Tabulka({ input }: { input: ReturnType<typeof buildInput> }) {
  if (!input) return null;
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
              <th className="py-1 pr-3 font-normal">otvára</th>
              <th className="py-1 pr-3 font-normal">pozícií</th>
              <th className="py-1 pr-3 font-normal">marža</th>
              <th className="py-1 font-normal">doliať</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.price} className="border-t border-hair">
                <td className="py-1 pr-3">{s.price.toFixed(4)}</td>
                <td className="py-1 pr-3">
                  {s.opensLong && s.opensShort ? "long + short"
                    : s.opensLong ? "len longy"
                    : s.opensShort ? "len shorty" : "nič"}
                </td>
                <td className="py-1 pr-3">{s.longs}L / {s.shorts}S</td>
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

/**
 * Kľúčové hladiny ako dlaždice, nie ako popisky v grafe — na osi dlhej
 * 3 000 pipov ležia pár pixelov od seba a v grafe by sa prekryli.
 */
function Hladiny({
  px, call, stop, input,
}: {
  px: number; call: number | null; stop: number | null;
  input: NonNullable<ReturnType<typeof buildInput>>;
}) {
  const pipov = (p: number) => Math.round((px - p) * 10000);
  const items: [string, string, string, string][] = [
    ["Teraz", fmtPrice(px), "var(--ink)", ""],
    ["⚠ Margin call pri", call == null ? "—" : fmtPrice(call), "var(--warn)",
      call == null ? "" : `${pipov(call)} pipov nižšie`],
    ["⚠ Broker zatvára pri", stop == null ? "—" : fmtPrice(stop), "var(--neg)",
      stop == null ? "" : `${pipov(stop)} pipov nižšie`],
  ];
  const na110 = snapshotAt(input, 1.10).fund100;
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-3 gap-2">
        {items.map(([k, v, c, note]) => (
          <div key={k} className="rounded-md bg-surface px-2 py-1.5">
            <div className="text-[10px] leading-tight text-faint">{k}</div>
            <div className="tabular-nums text-sm font-semibold" style={{ color: c }}>
              {v}
            </div>
            {note && <div className="text-[10px] text-faint">{note}</div>}
          </div>
        ))}
      </div>
      <p className="text-xs text-muted">
        Udržať účet až po kurz 1,1000 by si vyžiadalo doliať{" "}
        <b className="tabular-nums" style={{ color: "var(--fund)" }}>
          {money(na110, 0)}
        </b>
        .
      </p>
    </div>
  );
}
