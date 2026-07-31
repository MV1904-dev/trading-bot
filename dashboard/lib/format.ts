export const money = (v: number | null | undefined, digits = 2) =>
  v == null
    ? "—"
    : new Intl.NumberFormat("sk-SK", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }).format(v);

export const signed = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : (v >= 0 ? "+" : "") + money(v, digits);

export const price = (v: number | null | undefined) =>
  v == null ? "—" : v.toFixed(5);

export const pct = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : `${(v * 100).toFixed(digits)} %`;

/** Trvanie v milisekundách → "3 d 4 h" / "4 h 12 m" / "12 m". */
export function humanDuration(ms: number) {
  const m = Math.floor(ms / 60000);
  const h = Math.floor(m / 60);
  const d = Math.floor(h / 24);
  if (d > 0) return `${d} d ${h % 24} h`;
  if (h > 0) return `${h} h ${m % 60} m`;
  return `${m} m`;
}

/** Vek otvorenej pozície. */
export const age = (from: string, to: Date = new Date()) =>
  humanDuration(to.getTime() - new Date(from).getTime());

/** Ako dlho bola pozícia otvorená pred zatvorením. */
export const heldFor = (from: string, to: string | null) =>
  to == null ? "—" : humanDuration(new Date(to).getTime() - new Date(from).getTime());

export const holdMs = (from: string, to: string | null) =>
  (to ? new Date(to).getTime() : Date.now()) - new Date(from).getTime();

const DAY = 86_400_000;

/**
 * Farba podľa dĺžky držania: > 7 dní červená, > 3 dni žltá.
 * Grid má typicky cykly v hodinách — čo visí dni, drží kapitál a platí
 * funding, preto to má byť vidieť na prvý pohľad.
 */
export const holdClass = (ms: number) =>
  ms > 7 * DAY
    ? "text-neg font-medium"
    : ms > 3 * DAY
      ? "text-warn"
      : "text-muted";

export const dateTime = (v: string | null) =>
  v == null
    ? "—"
    : new Date(v).toLocaleString("sk-SK", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });

export const dateOnly = (v: string) =>
  new Date(v).toLocaleDateString("sk-SK", { day: "2-digit", month: "2-digit" });

/** Trieda pre kladné/záporné číslo. Nula sa počíta ako neutrálna. */
export const pnlClass = (v: number | null | undefined) =>
  v == null || v === 0 ? "text-faint" : v > 0 ? "text-pos" : "text-neg";
