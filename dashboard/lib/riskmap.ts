/**
 * Model „čo urobí bot a koľko to bude stáť" pre ľubovoľný kurz.
 *
 * Pásmo nie je symetrická ochrana: `band_low` vypína len shorty, `band_high`
 * len longy. Pod dolnou hranou teda longy pribúdajú ďalej, až kým sa nenaplní
 * kapacita strany — a práve to je vec, ktorú z čísel v hlavičke nikto
 * nevyčíta. Mapa to počíta dopredu pre celú os.
 *
 * Zámerné zjednodušenia (model, nie predpoveď):
 *  - cesta je jednosmerná; ak kurz po ceste osciluje, longy vyberú TP,
 *    marža sa uvoľní a skutočnosť bude lepšia než tieto čísla
 *  - swap sa nepripočítava (závisí od toho, ako dlho cesta trvá)
 *  - marža na pozíciu sa berie zo živého stavu (použitá marža / počet
 *    pozícií), takže sedí s brokerom aj keď sa zmení páka
 */

export type Leg = { side: "long" | "short"; entry: number; tp: number | null; qty: number };

export type MapInput = {
  price: number;
  balance: number;
  marginPer: number;
  bandLow: number;
  bandHigh: number;
  stepLong: number;
  stepShort: number;
  atr: number;
  atrMult: number;
  qty: number;
  cap: number;
  refLong: number;
  longEnabled: boolean;
  shortEnabled: boolean;
  legs: Leg[];
};

export type Snap = {
  price: number;
  longs: number;
  shorts: number;
  used: number;
  equity: number;
  level: number | null;
  /** Koľko treba doliať, aby margin level dosiahol danú hranicu. */
  fund100: number;
  fund50: number;
  opensLong: boolean;
  opensShort: boolean;
};

/** Zisk/strata nohy pri danom kurze, v mene účtu (EUR). */
const legPnl = (l: Leg, px: number) =>
  ((l.side === "long" ? px - l.entry : l.entry - px) * l.qty) / px;

/** Zasiahol kurz TP tejto nohy? Long zavrie hore, short dole. */
const hitTp = (l: Leg, px: number) =>
  l.tp != null && (l.side === "long" ? px >= l.tp : px <= l.tp);

/**
 * Koľko ďalších longov stihne grid otvoriť cestou z `from` na `to`.
 * Kotva sa po vstupe presúva na cenu vstupu, takže kroky idú od nej —
 * rovnako ako v strategy_grid25.next_long_level().
 */
function extraLongs(i: MapInput, to: number, volnych: number): number[] {
  if (!i.longEnabled || to >= i.price || volnych <= 0) return [];
  const out: number[] = [];
  let ref = i.refLong || i.price;
  while (out.length < volnych) {
    const next = ref - Math.max(ref * i.stepLong, i.atrMult * i.atr);
    if (next < to) break;
    if (next >= i.bandHigh) break;      // nad hornou hranou sa long neotvára
    out.push(next);
    ref = next;
  }
  return out;
}

export function snapshotAt(i: MapInput, px: number): Snap {
  let balance = i.balance;
  const open: Leg[] = [];
  for (const l of i.legs) {
    if (hitTp(l, px)) balance += legPnl(l, l.tp!);
    else open.push(l);
  }
  const volnych = i.cap - open.filter((l) => l.side === "long").length;
  const pridane = extraLongs(i, px, volnych);

  const floating =
    open.reduce((a, l) => a + legPnl(l, px), 0) +
    pridane.reduce((a, e) => a + legPnl({ side: "long", entry: e, tp: null, qty: i.qty }, px), 0);

  const longs = open.filter((l) => l.side === "long").length + pridane.length;
  const shorts = open.length - (longs - pridane.length);
  const used = (longs + shorts) * i.marginPer;
  const equity = balance + floating;

  return {
    price: px,
    longs,
    shorts,
    used,
    equity,
    level: used > 0 ? (equity / used) * 100 : null,
    fund100: used > 0 ? Math.max(0, used - equity) : 0,
    fund50: used > 0 ? Math.max(0, 0.5 * used - equity) : 0,
    opensLong: i.longEnabled && px < i.bandHigh,
    opensShort: i.shortEnabled && px > i.bandLow,
  };
}

/** Rovnomerná vzorka celej osi; `step` je v cene, nie v pipoch. */
export function scan(i: MapInput, from: number, to: number, step = 0.0005): Snap[] {
  const out: Snap[] = [];
  for (let n = Math.round(from / step); n <= Math.round(to / step); n++) {
    out.push(snapshotAt(i, Number((n * step).toFixed(6))));
  }
  return out;
}

/** Najvyšší kurz, pri ktorom margin level klesne na danú hranicu. */
export function thresholdPrice(snaps: Snap[], level: number): number | null {
  const pod = snaps.filter((s) => s.level != null && s.level < level);
  return pod.length ? Math.max(...pod.map((s) => s.price)) : null;
}

/** Zostaví vstup modelu zo živého stavu bota a otvorených pozícií. */
export function buildInput(
  state: {
    last_price: number | null; balance: number | null; used_margin: number | null;
    band_low: number | null; band_high: number | null;
    config: Record<string, unknown> | null;
  },
  positions: { side: "long" | "short"; entry_price: number; tp_price: number | null; qty: number }[],
): MapInput | null {
  const c = (state.config ?? {}) as Record<string, number | boolean | null>;
  const price = state.last_price;
  if (price == null || state.balance == null || positions.length === 0) return null;

  const qty = Number(c.qty) || positions[0].qty || 10_000;
  const marginPer = (state.used_margin ?? 0) / positions.length || qty / 30;

  return {
    price,
    balance: state.balance,
    marginPer,
    bandLow: state.band_low ?? 1.12,
    bandHigh: state.band_high ?? 1.16,
    stepLong: Number(c.step_long) || 0.0015,
    stepShort: Number(c.step_short) || 0.001,
    atr: Number(c.atr) || 0,
    atrMult: Number(c.atr_mult) || 2,
    qty,
    cap: (Number(c.base_levels) || 20) + (Number(c.reserve_levels) || 10),
    refLong: Number(c.ref_long) || price,
    longEnabled: c.long_enabled !== false,
    shortEnabled: c.short_enabled !== false,
    legs: positions.map((p) => ({
      side: p.side, entry: p.entry_price, tp: p.tp_price, qty: p.qty,
    })),
  };
}
