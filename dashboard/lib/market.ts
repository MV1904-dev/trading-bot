/**
 * Je forexový trh zatvorený? Zrkadlo `_market_closed()` z bot_ctrader.py —
 * týždeň beží od nedeľného otvorenia do piatkového zatvorenia o 17:00 NY,
 * hranice s rezervou (piatok od 16:55, nedeľa do 17:10 NY).
 *
 * Dashboard to potrebuje samostatne: cez víkend bot legitímne nemá cenu zo
 * streamu a bez tejto kontroly by hlásil výpadok každú sobotu a nedeľu.
 * Keď sa hranice zmenia v botovi, treba ich zmeniť aj tu.
 */
const NY_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function fxMarketClosed(now: number): boolean {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(now));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  const wd = NY_DAYS.indexOf(get("weekday"));       // pondelok = 0
  const mins = Number(get("hour")) * 60 + Number(get("minute"));
  if (wd < 0) return false;
  if (wd === 5) return true;                        // sobota celá
  if (wd === 4 && mins >= 16 * 60 + 55) return true; // piatok od 16:55 NY
  if (wd === 6 && mins < 17 * 60 + 10) return true;  // nedeľa do 17:10 NY
  return false;
}

/**
 * Časová zóna, v ktorej bot delí obchody na dni (cfg.TIMEZONE).
 *
 * Dni v `daily_cycles` vznikajú v tejto zóne, nie v UTC — obchod zavretý
 * o 00:51 v Bratislave patrí do toho dňa, hoci v UTC je 22:51 predošlého.
 * Keď sa zóna zmení v botovi, treba ju zmeniť aj tu, inak by dlaždica
 * „Dnes" hľadala deň, ktorý bot takto nepomenoval.
 */
export const BOT_TZ = "Europe/Bratislava";

/** Dnešný dátum v čase bota ako YYYY-MM-DD — kľúč do `daily_cycles`. */
export function botToday(now: number = Date.now()): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: BOT_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(now));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}
