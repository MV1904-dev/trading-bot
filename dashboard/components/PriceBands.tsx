"use client";

import { price } from "@/lib/format";
import type { BotState } from "@/lib/types";

/**
 * Kurz v kontexte gridových pásiem. Zámerne bez grafu — jediné, čo treba
 * vidieť na prvý pohľad, je či cena nevyliezla z pásma, kde grid obchoduje.
 */
export default function PriceBands({ state }: { state: BotState | null }) {
  const lo = state?.band_low ?? null;
  const hi = state?.band_high ?? null;
  const px = state?.last_price ?? null;
  if (lo == null || hi == null) return null;

  const clamped = px == null ? null : Math.min(Math.max(px, lo), hi);
  const pos = clamped == null ? 50 : ((clamped - lo) / (hi - lo)) * 100;
  const outside = px != null && (px < lo || px > hi);

  return (
    <div className="card">
      <div className="flex items-baseline justify-between">
        <span className="card-title">{state?.symbol ?? "EURUSD"}</span>
        <span className="font-mono text-lg">{price(px)}</span>
      </div>

      <div className="relative mt-4 h-2 rounded-full bg-line">
        <div
          className={`absolute top-1/2 h-4 w-1 -translate-y-1/2 rounded
                      ${outside ? "bg-rose-400" : "bg-emerald-400"}`}
          style={{ left: `calc(${pos}% - 2px)` }}
        />
      </div>
      <div className="mt-2 flex justify-between text-xs text-faint">
        <span>{price(lo)}</span>
        <span>{price(hi)}</span>
      </div>

      {outside && (
        <p className="mt-3 text-sm text-neg">
          Kurz je mimo gridového pásma — nové vstupy sa neotvárajú.
        </p>
      )}
    </div>
  );
}
