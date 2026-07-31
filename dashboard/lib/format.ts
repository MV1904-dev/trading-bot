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

/** Vek pozície: 3 d 4 h / 4 h 12 m / 12 m */
export function age(from: string, to: Date = new Date()) {
  const ms = to.getTime() - new Date(from).getTime();
  const m = Math.floor(ms / 60000);
  const h = Math.floor(m / 60);
  const d = Math.floor(h / 24);
  if (d > 0) return `${d} d ${h % 24} h`;
  if (h > 0) return `${h} h ${m % 60} m`;
  return `${m} m`;
}

export const dateTime = (v: string | null) =>
  v == null
    ? "—"
    : new Date(v).toLocaleString("sk-SK", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });

/** Trieda pre kladné/záporné číslo. Nula sa počíta ako neutrálna. */
export const pnlClass = (v: number | null | undefined) =>
  v == null || v === 0
    ? "text-zinc-400"
    : v > 0
      ? "text-emerald-400"
      : "text-rose-400";
