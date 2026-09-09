"use client";

import { price as fmtPrice } from "@/lib/format";
import type { BotState } from "@/lib/types";

/**
 * Kde je kurz a kam musí dôjsť pre najbližší vstup.
 *
 * Spúšť sa v stratégii počíta od kotvy (ref_long/ref_short), a tá už môže byť
 * prekročená — vtedy vstup padne na najbližšom bare, čiže na aktuálnom kurze.
 * Zobrazujeme preto vždy úroveň, KAM MUSÍ KURZ DÔJSŤ, nie tú spočítanú dozadu.
 *
 * Po vstupe sa kotva presunie na cenu vstupu, takže ďalšie úrovne idú po kroku
 * od nej — z toho je mriežka dopredu.
 */

type Cfg = {
  step_short?: number; step_long?: number;
  short_enabled?: boolean; long_enabled?: boolean;
  ref_long?: number | null; ref_short?: number | null;
  atr?: number | null; atr_mult?: number;
};

type Level = { p: number; allowed: boolean };

const pipw = (n: number) => (n === 1 ? "pip" : n >= 2 && n <= 4 ? "pipy" : "pipov");
const pips = (n: number) => `${n} ${pipw(n)}`;
const pipsTo = (a: number, b: number) => Math.round(Math.abs(a - b) * 10000);
const p4 = (v: number) =>
  v.toLocaleString("sk-SK", { minimumFractionDigits: 4, maximumFractionDigits: 4 });

function ladder(
  side: "long" | "short", first: number, step: number,
  lo: number, hi: number, atr: number, atrMult: number,
): Level[] {
  const out: Level[] = [];
  let p = first;
  for (let i = 0; i < 3; i++) {
    out.push({ p, allowed: side === "short" ? p > lo : p < hi });
    p = side === "short" ? p * (1 + step) : p - Math.max(p * step, atrMult * atr);
  }
  return out;
}

export default function GridBand({ state }: { state: BotState | null }) {
  const cfg = (state?.config ?? {}) as Cfg;
  const px = state?.last_price ?? null;
  const lo = state?.band_low ?? null;
  const hi = state?.band_high ?? null;
  const refL = cfg.ref_long ?? null;
  const refS = cfg.ref_short ?? null;

  if (px == null || lo == null || hi == null || refL == null || refS == null) {
    return (
      <p className="py-6 text-center text-sm text-muted">
        Čakám na kurz a kotvy z bota.
      </p>
    );
  }

  const atr = cfg.atr ?? 0;
  const atrMult = cfg.atr_mult ?? 2;
  const stepS = cfg.short_enabled === false ? null : cfg.step_short ?? null;
  const stepL = cfg.long_enabled === false ? null : cfg.step_long ?? null;

  // spúšť z kotvy; ak je prekročená, vstup padne na aktuálnom kurze
  const rawS = stepS == null ? null : refS + refS * stepS;
  const rawL = stepL == null ? null : refL - Math.max(refL * stepL, atrMult * atr);
  const ladS = stepS == null || rawS == null ? []
    : ladder("short", Math.max(rawS, px), stepS, lo, hi, atr, atrMult);
  const ladL = stepL == null || rawL == null ? []
    : ladder("long", Math.min(rawL, px), stepL, lo, hi, atr, atrMult);

  const idxS = ladS.findIndex((l) => l.allowed);
  const idxL = ladL.findIndex((l) => l.allowed);
  const mainS = idxS >= 0 ? ladS[idxS] : null;
  const mainL = idxL >= 0 ? ladL[idxL] : null;

  // --- mierka ---------------------------------------------------------------
  // Pásmo má stovky pipov, dianie desiatky. Hlavná os je preto výrez okolo
  // diania; celé pásmo nesie úzky prúžok nad ním.
  const W = 900, X0 = 46, X1 = 854, AXIS = 150;
  const act = [px, ...ladS.map((l) => l.p), ...ladL.map((l) => l.p)];
  [lo, hi].forEach((e) => { if (Math.abs(e - px) < 0.008) act.push(e); });
  let min = Math.min(...act), max = Math.max(...act);
  if (max - min < 0.0026) { const c = (max + min) / 2; min = c - 0.0013; max = c + 0.0013; }
  const pad = (max - min) * 0.16;
  min -= pad; max += pad;
  const x = (p: number) => X0 + ((p - min) / (max - min)) * (X1 - X0);
  const inView = (p: number) => p >= min && p <= max;

  const oLo = Math.min(lo, px) - 0.004, oHi = Math.max(hi, px) + 0.004;
  const ox = (p: number) => X0 + ((p - oLo) / (oHi - oLo)) * (X1 - X0);

  const span = max - min;
  const tick = span > 0.02 ? 0.005 : span > 0.008 ? 0.002 : 0.0005;
  const ticks: number[] = [];
  for (let p = Math.ceil(min / tick) * tick; p <= max; p += tick) ticks.push(p);

  const bandX0 = inView(lo) ? x(lo) : lo < min ? X0 : null;
  const bandX1 = inView(hi) ? x(hi) : hi > max ? X1 : null;

  const sides = [
    { lad: ladS, main: mainS, idx: idxS, color: "var(--short)", label: "short",
      on: stepS != null },
    { lad: ladL, main: mainL, idx: idxL, color: "var(--long)", label: "long",
      on: stepL != null },
  ];

  const where = px > hi ? "nad pásmom" : px < lo ? "pod pásmom" : "v pásme";
  const verdict =
    mainS && mainL ? "V pásme — čaká na obchod"
    : !mainS && !mainL ? "Neotvára nič"
    : `Jednostranne — otvára len ${mainS ? "short" : "long"}y`;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-sm font-medium">{verdict}</span>
        <span className="text-xs text-faint">
          kurz {fmtPrice(px)} · {where} · pásmo {p4(lo)}–{p4(hi)}
        </span>
      </div>

      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} 262`} className="block h-auto w-full min-w-[560px]"
             role="img" aria-label="Kurz a najbližšie vstupné úrovne gridu">
          {/* prehľadový prúžok: celé pásmo */}
          <rect x={X0} y={30} width={X1 - X0} height={15} fill="var(--hair)" rx={2} />
          <rect x={ox(lo)} y={30} width={Math.max(0, ox(hi) - ox(lo))} height={15}
                fill="var(--surface)" />
          {[lo, hi].map((e) => (
            <line key={e} x1={ox(e)} y1={30} x2={ox(e)} y2={45}
                  stroke="var(--line)" strokeWidth={1.5} />
          ))}
          <line x1={ox(px)} y1={25} x2={ox(px)} y2={50} stroke="var(--ink)" strokeWidth={2.5} />
          <rect x={ox(Math.max(min, oLo))} y={27}
                width={Math.max(2, ox(Math.min(max, oHi)) - ox(Math.max(min, oLo)))}
                height={21} fill="none" stroke="var(--line)" strokeWidth={1}
                strokeDasharray="3 3" />
          <text x={X0} y={20} fill="var(--faint)" fontSize={11}>
            celé pásmo · {p4(lo)} – {p4(hi)}
          </text>
          <text x={X1} y={20} textAnchor="end" fill="var(--faint)" fontSize={11}>
            prerušovane = výrez dole
          </text>

          {/* pásmo vo výreze */}
          {bandX0 != null && bandX1 != null && bandX1 > bandX0 && (
            <rect x={bandX0} y={AXIS - 30} width={bandX1 - bandX0} height={30}
                  fill="var(--surface)" />
          )}
          {([[lo, "dolná hrana"], [hi, "horná hrana"]] as [number, string][])
            .filter(([p]) => inView(p))
            .map(([p, lab]) => (
              <g key={lab}>
                <line x1={x(p)} y1={AXIS - 34} x2={x(p)} y2={AXIS}
                      stroke="var(--line)" strokeWidth={2} />
                <text x={x(p) + 5} y={AXIS - 38} fill="var(--muted)" fontSize={11}>
                  {lab} {p4(p)}
                </text>
              </g>
            ))}

          {/* os */}
          <line x1={X0} y1={AXIS} x2={X1} y2={AXIS} stroke="var(--line)" strokeWidth={1} />
          {ticks.map((p) => (
            <g key={p}>
              <line x1={x(p)} y1={AXIS} x2={x(p)} y2={AXIS + 5}
                    stroke="var(--line)" strokeWidth={1} />
              <text x={x(p)} y={AXIS + 20} textAnchor="middle" fill="var(--faint)"
                    fontSize={11} className="tabular-nums">
                {fmtPrice(p)}
              </text>
            </g>
          ))}

          {/* úrovne mriežky: popísaná je tá, ktorá naozaj otvorí */}
          {sides.map((s) =>
            s.lad.filter((l) => inView(l.p)).map((l, i) => {
              const isMain = s.on && s.idx >= 0 ? l === s.main : i === 0;
              const live = s.on && l.allowed;
              const op = live ? (isMain ? 1 : 0.5) : 0.3;
              const d = pipsTo(l.p, px);
              return (
                <g key={`${s.label}-${l.p}`}>
                  <line x1={x(l.p)} y1={AXIS - (isMain ? 30 : 16)}
                        x2={x(l.p)} y2={AXIS + (isMain ? 34 : 14)}
                        stroke={s.color} strokeWidth={isMain ? 2 : 1.5}
                        strokeDasharray="5 4" strokeOpacity={op} />
                  {isMain && (
                    <>
                      <text x={x(l.p)} y={AXIS + 56} textAnchor="middle"
                            fill={live ? "var(--ink)" : "var(--faint)"}
                            fontSize={12.5} fontWeight={live ? 500 : 400}
                            className="tabular-nums">
                        {s.label} {fmtPrice(l.p)}
                      </text>
                      <text x={x(l.p)} y={AXIS + 71} textAnchor="middle"
                            fill="var(--faint)" fontSize={11}>
                        {!s.on ? "strana vypnutá — drahé držanie"
                          : !l.allowed ? "až za hranou pásma"
                          : d === 0 ? "spustí sa hneď"
                          : `${pips(d)} ${l.p > px ? "vyššie" : "nižšie"}`}
                      </text>
                    </>
                  )}
                </g>
              );
            }),
          )}

          {/* kurz */}
          <line x1={x(px)} y1={AXIS - 52} x2={x(px)} y2={AXIS}
                stroke="var(--ink)" strokeWidth={2.5} />
          <circle cx={x(px)} cy={AXIS} r={4.5} fill="var(--ink)"
                  stroke="var(--bg)" strokeWidth={2} />
          <text x={x(px)} y={AXIS - 60} fill="var(--ink)" fontSize={14} fontWeight={600}
                className="tabular-nums"
                textAnchor={x(px) > X1 - 90 ? "end" : x(px) < X0 + 90 ? "start" : "middle"}>
            kurz {fmtPrice(px)}
          </text>

          {/* hrany, ktoré sa do výrezu nezmestili */}
          {[
            !inView(hi) && `horná hrana ${p4(hi)} je ${pips(pipsTo(hi, px))} ${hi > px ? "nad" : "pod"} kurzom`,
            !inView(lo) && `dolná hrana ${p4(lo)} je ${pips(pipsTo(lo, px))} ${lo > px ? "nad" : "pod"} kurzom`,
          ].filter(Boolean).map((t, i) => (
            <text key={i} x={X0} y={244 + i * 15} fill="var(--faint)" fontSize={11}>
              {t}
            </text>
          ))}
        </svg>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
        {([
          ["Otvorí short pri", stepS == null ? "vypnutý — drahé držanie"
            : !mainS ? "za hranou pásma"
            : pipsTo(mainS.p, px) === 0 ? "hneď"
            : `${fmtPrice(mainS.p)} · ${pips(pipsTo(mainS.p, px))} vyššie`],
          ["Otvorí long pri", stepL == null ? "vypnutý — drahé držanie"
            : !mainL ? `až pod ${p4(hi)}`
            : pipsTo(mainL.p, px) === 0 ? "hneď"
            : `${fmtPrice(mainL.p)} · ${pips(pipsTo(mainL.p, px))} nižšie`],
          ["Horná hrana", `${p4(hi)} · ${pips(pipsTo(hi, px))} ${hi >= px ? "nad" : "pod"}`],
          ["Dolná hrana", `${p4(lo)} · ${pips(pipsTo(lo, px))} ${lo >= px ? "nad" : "pod"}`],
        ] as [string, string][]).map(([k, v]) => (
          <div key={k}>
            <dt className="text-faint">{k}</dt>
            <dd className="tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
