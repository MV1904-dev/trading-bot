/**
 * Model „čo urobí bot a koľko to bude stáť" pre ľubovoľný kurz.
 *
 * Pásmo nie je symetrická ochrana: `band_low` vypína len shorty, `band_high`
 * len longy. Pod dolnou hranou teda longy pribúdajú ďalej a nad hornou
 * shorty, až kým sa nenaplní kapacita strany — a práve to je vec, ktorú
 * z čísel v hlavičke nikto nevyčíta. Mapa to počíta dopredu pre celú os,
 * oboma smermi: pri páde rastú longy, pri raste shorty.
 *
 * Zámerné zjednodušenia (model, nie predpoveď):
 *  - cesta je jednosmerná; ak kurz po ceste osciluje, longy vyberú TP,
 *    marža sa uvoľní a skutočnosť bude lepšia než tieto čísla
 *  - budúci swap sa nepripočítava (závisí od toho, ako dlho cesta trvá);
 *    už naakumulovaný swap otvorených pozícií áno — je to vzniknutý náklad
 *  - marža na pozíciu sa berie zo živého stavu (použitá marža / počet
 *    pozícií), takže sedí s brokerom aj keď sa zmení páka
 *  - použitá marža je len z väčšej strany: cTrader pri protismerných
 *    pozíciách na jednom páre účtuje hedging (overené proti aplikácii
 *    brokera 15. 10. 2026: 25 L / 3 S → 25 × 333,33 = 8 333,25)
 */

export type Leg = { side: "long" | "short"; entry: number; tp: number | null; qty: number };

export type MapInput = {
  price: number;
  balance: number;
  /** Naakumulovaný swap otvorených pozícií (záporný = náklad). */
  swap: number;
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
  refShort: number;
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

/**
 * Koľko ďalších shortov grid otvorí cestou z `from` hore na `to`. Short
 * nemá ATR podlahu, len krok od kotvy — ako strategy_grid25 (rise ≥
 * ref × step_short). Nad hornou hranou pásma sa shorty otvárajú ďalej.
 */
function extraShorts(i: MapInput, to: number, volnych: number): number[] {
  if (!i.shortEnabled || to <= i.price || volnych <= 0) return [];
  const out: number[] = [];
  let ref = i.refShort || i.price;
  while (out.length < volnych) {
    // Pod dolnou hranou sa short neotvára — padne na prvom kurze nad ňou.
    const next = Math.max(ref * (1 + i.stepShort), i.bandLow + 0.00001);
    if (next > to) break;
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
  const openL = open.filter((l) => l.side === "long").length;
  const openS = open.length - openL;
  const pridaneL = extraLongs(i, px, i.cap - openL);
  const pridaneS = extraShorts(i, px, i.cap - openS);

  const floating =
    open.reduce((a, l) => a + legPnl(l, px), 0) +
    pridaneL.reduce((a, e) => a + legPnl({ side: "long", entry: e, tp: null, qty: i.qty }, px), 0) +
    pridaneS.reduce((a, e) => a + legPnl({ side: "short", entry: e, tp: null, qty: i.qty }, px), 0);

  const longs = openL + pridaneL.length;
  const shorts = openS + pridaneS.length;
  const used = Math.max(longs, shorts) * i.marginPer;
  const equity = balance + floating + i.swap;

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

/** Ceny, na ktorých by grid cestou dole otvoril ďalšie longy (až po kapacitu). */
export function futureLongs(i: MapInput, to: number): number[] {
  const open = i.legs.filter((l) => l.side === "long").length;
  return extraLongs(i, to, i.cap - open);
}

/** Ceny, na ktorých by grid cestou hore otvoril ďalšie shorty (až po kapacitu). */
export function futureShorts(i: MapInput, to: number): number[] {
  const open = i.legs.filter((l) => l.side === "short").length;
  return extraShorts(i, to, i.cap - open);
}

/** Rovnomerná vzorka celej osi; `step` je v cene, nie v pipoch. */
export function scan(i: MapInput, from: number, to: number, step = 0.0005): Snap[] {
  const out: Snap[] = [];
  for (let n = Math.round(from / step); n <= Math.round(to / step); n++) {
    out.push(snapshotAt(i, Number((n * step).toFixed(6))));
  }
  return out;
}

/**
 * Prvý kurz od aktuálneho daným smerom, pri ktorom margin level klesne pod
 * hranicu: pri páde najvyšší taký kurz pod `from`, pri raste najnižší nad ním.
 */
export function thresholdPrice(
  snaps: Snap[], level: number, from: number, dir: "down" | "up" = "down",
): number | null {
  const pod = snaps.filter((s) => s.level != null && s.level < level &&
    (dir === "down" ? s.price <= from : s.price >= from));
  if (!pod.length) return null;
  const ceny = pod.map((s) => s.price);
  return dir === "down" ? Math.max(...ceny) : Math.min(...ceny);
}

/** Zostaví vstup modelu zo živého stavu bota a otvorených pozícií. */
export function buildInput(
  state: {
    last_price: number | null; balance: number | null; used_margin: number | null;
    band_low: number | null; band_high: number | null;
    config: Record<string, unknown> | null;
  },
  positions: { side: "long" | "short"; entry_price: number; tp_price: number | null;
               qty: number; funding_usd?: number | null }[],
): MapInput | null {
  const c = (state.config ?? {}) as Record<string, number | boolean | null>;
  const price = state.last_price;
  if (price == null || state.balance == null || positions.length === 0) return null;

  const qty = Number(c.qty) || positions[0].qty || 10_000;
  const marginPer = (state.used_margin ?? 0) / positions.length || qty / 30;

  return {
    price,
    balance: state.balance,
    swap: positions.reduce((a, p) => a + (Number(p.funding_usd) || 0), 0),
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
    refShort: Number(c.ref_short) || price,
    longEnabled: c.long_enabled !== false,
    shortEnabled: c.short_enabled !== false,
    legs: positions.map((p) => ({
      side: p.side, entry: p.entry_price, tp: p.tp_price, qty: p.qty,
    })),
  };
}
