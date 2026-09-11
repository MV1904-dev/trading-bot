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

  // --- mierka: kurz je vždy v strede -----------------------------------------
  // Symetricky okolo kurzu, aby sa dalo od stredu čítať "koľko hore, koľko
  // dole". Rozsah určuje vzdialenejší z dvoch najbližších vstupov; hranu pásma
  // priberie, keď je v dosahu, nech je vidieť, kde sa strana vypne.
  const W = 460, X0 = 24, X1 = 436, AXIS = 104;
  const dists = [mainS, mainL].filter(Boolean).map((l) => Math.abs(l!.p - px));
  let half = Math.max(...dists, 0.0006) * 1.7;
  [lo, hi].forEach((e) => {
    const d = Math.abs(e - px);
    if (d < half * 1.5) half = Math.max(half, d * 1.18);
  });
  const min = px - half, max = px + half;
  const x = (p: number) => X0 + ((p - min) / (max - min)) * (X1 - X0);
  const inView = (p: number) => p >= min && p <= max;

  const bandX0 = inView(lo) ? x(lo) : lo < min ? X0 : null;
  const bandX1 = inView(hi) ? x(hi) : hi > max ? X1 : null;

  const sides = [
    { lad: ladS, main: mainS, idx: idxS, color: "var(--short)", label: "short", on: stepS != null },
    { lad: ladL, main: mainL, idx: idxL, color: "var(--long)", label: "long", on: stepL != null },
  ];

  const rows: [string, string][] = [
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
    ["Ďalšie short úrovne", ladS.length > 1 ? ladS.slice(1).map((l) => fmtPrice(l.p)).join(" · ") : "—"],
    ["Ďalšie long úrovne", ladL.length > 1 ? ladL.slice(1).map((l) => fmtPrice(l.p)).join(" · ") : "—"],
  ];

  return (
    <div className="flex flex-col gap-2">
      <svg viewBox={`0 0 ${W} 200`} className="block h-auto w-full"
           role="img" aria-label="Kurz a najbližšie vstupné úrovne gridu">
        {/* pásmo */}
        {bandX0 != null && bandX1 != null && bandX1 > bandX0 && (
          <rect x={bandX0} y={AXIS - 26} width={bandX1 - bandX0} height={26}
                fill="var(--surface)" />
        )}
        {([[lo, "dolná hrana"], [hi, "horná hrana"]] as [number, string][])
          .filter(([p]) => inView(p))
          .map(([p, lab]) => (
            <g key={lab}>
              <line x1={x(p)} y1={AXIS - 30} x2={x(p)} y2={AXIS}
                    stroke="var(--line)" strokeWidth={2} />
              <text x={x(p)} y={AXIS - 36} textAnchor="middle"
                    fill="var(--faint)" fontSize={11}>
                {lab} {p4(p)}
              </text>
            </g>
          ))}

        <line x1={X0} y1={AXIS} x2={X1} y2={AXIS} stroke="var(--line)" strokeWidth={1} />

        {/* úrovne mriežky — popísaná je tá, ktorá naozaj otvorí */}
        {sides.map((s) =>
          s.lad.filter((l) => inView(l.p)).map((l, i) => {
            const isMain = s.on && s.idx >= 0 ? l === s.main : i === 0;
            const live = s.on && l.allowed;
            const op = live ? (isMain ? 1 : 0.45) : 0.3;
            const d = pipsTo(l.p, px);
            return (
              <g key={`${s.label}-${l.p}`}>
                <line x1={x(l.p)} y1={AXIS - (isMain ? 26 : 13)}
                      x2={x(l.p)} y2={AXIS + (isMain ? 26 : 12)}
                      stroke={s.color} strokeWidth={isMain ? 2 : 1.5}
                      strokeDasharray="5 4" strokeOpacity={op} />
                {isMain && (
                  <>
                    <text x={x(l.p)} y={AXIS + 46} textAnchor="middle"
                          fill={live ? "var(--ink)" : "var(--faint)"}
                          fontSize={13} fontWeight={live ? 500 : 400}
                          className="tabular-nums">
                      {s.label} {fmtPrice(l.p)}
                    </text>
                    <text x={x(l.p)} y={AXIS + 62} textAnchor="middle"
                          fill="var(--faint)" fontSize={11}>
                      {!s.on ? "strana vypnutá"
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

        {/* kurz — vždy v strede */}
        <line x1={x(px)} y1={AXIS - 44} x2={x(px)} y2={AXIS}
              stroke="var(--ink)" strokeWidth={2.5} />
        <circle cx={x(px)} cy={AXIS} r={4.5} fill="var(--ink)"
                stroke="var(--bg)" strokeWidth={2} />
        <text x={x(px)} y={AXIS - 52} textAnchor="middle" fill="var(--ink)"
              fontSize={15} fontWeight={600} className="tabular-nums">
          {fmtPrice(px)}
        </text>

        {/* hrany, ktoré sa do výrezu nezmestili */}
        {[
          !inView(hi) && `horná hrana ${p4(hi)} je ${pips(pipsTo(hi, px))} ${hi > px ? "nad" : "pod"}`,
          !inView(lo) && `dolná hrana ${p4(lo)} je ${pips(pipsTo(lo, px))} ${lo > px ? "nad" : "pod"}`,
        ].filter(Boolean).map((t, i) => (
          <text key={i} x={X0} y={182 + i * 14} fill="var(--faint)" fontSize={11}>
            {t}
          </text>
        ))}
      </svg>

      <details className="group">
        <summary className="cursor-pointer list-none text-xs text-faint
                            hover:text-muted">
          Úrovne a hrany pásma
          <span className="ml-1 inline-block transition-transform
                           group-open:rotate-90">›</span>
        </summary>
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
          {rows.map(([k, v]) => (
            <div key={k}>
              <dt className="text-faint">{k}</dt>
              <dd className="tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
      </details>
    </div>
  );
}
