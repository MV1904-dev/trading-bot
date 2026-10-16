"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import ThemeToggle from "@/components/ThemeToggle";
import { useEnv, type BotEnv } from "@/lib/env";

/** Prepínač účtu. Live je predvolené — bot obchoduje naživo; SIM je
    virtuálny účet súťažnej stratégie Strážca, demo archív checkpointu. */
const ENV_LABEL: Record<BotEnv, string> = { live: "live", paper: "sim", demo: "demo" };
const ENV_TITLE: Record<BotEnv, string> = {
  live: "Živý účet — grid",
  paper: "Virtuálny účet — stratégia Strážca",
  demo: "Demo archív",
};
function EnvToggle() {
  const { env, setEnv } = useEnv();
  return (
    <div className="mr-1 flex overflow-hidden rounded-md border border-hair text-[11px] font-semibold">
      {(["live", "paper", "demo"] as BotEnv[]).map((e) => (
        <button
          key={e}
          onClick={() => setEnv(e)}
          aria-pressed={env === e}
          title={ENV_TITLE[e]}
          className={`px-2 py-1 uppercase transition-colors ${
            env === e
              ? e === "live"
                ? "bg-red-600 text-white"
                : e === "paper"
                  ? "bg-violet-600 text-white"
                  : "bg-surface text-ink"
              : "text-faint"
          }`}
        >
          {ENV_LABEL[e]}
        </button>
      ))}
    </div>
  );
}

/** Hlavné položky sa vojdú bez orezania popisku; História a Stratégie sa
    len prezerajú, nevyžadujú dennú akciu, tak idú pod „ďalšie". */
const MAIN = [
  { href: "/", label: "Prehľad", icon: HomeIcon },
  { href: "/pnl", label: "P/L", icon: ChartIcon },
  { href: "/mapa", label: "Mapa", icon: MapIcon },
  { href: "/calendar", label: "Kalendár", icon: CalendarIcon },
];
const MORE = [
  { href: "/vs", label: "Súťaž" },
  { href: "/history", label: "História" },
  { href: "/strategies", label: "Stratégie" },
];

export default function Nav() {
  const path = usePathname();
  const [more, setMore] = useState(false);
  if (path?.startsWith("/login")) return null;

  const all = [...MAIN, ...MORE];

  return (
    <>
      <header className="safe-top sticky top-0 z-10 border-b border-hair bg-bg/90 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center gap-1 px-4 py-2">
          {/* Značka aplikácie na každej stránke; na mobile za ňou názov
              stránky, na desktope ho nesie zvýraznená položka navigácie. */}
          <Link href="/" className="mr-1.5 flex items-center gap-2 text-[17px] sm:mr-4 font-bold tracking-tight text-ink">
            <BotMark size={26} />
            Bot
          </Link>
          <nav className="hidden flex-1 gap-1 sm:flex">
            {all.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className={`rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                  path === l.href
                    ? "bg-surface text-ink"
                    : "text-muted hover:text-ink"
                }`}
              >
                {l.label}
              </Link>
            ))}
          </nav>
          <span className="flex-1 truncate text-[15px] text-muted sm:hidden">
            {(() => {
              const label = all.find((l) => l.href === path)?.label;
              return label ? `— ${label}` : null;
            })()}
          </span>
          <EnvToggle />
          <ThemeToggle />
        </div>
      </header>

      <nav className="safe-bottom fixed inset-x-0 bottom-0 z-20 border-t border-hair bg-bg/95 backdrop-blur sm:hidden">
        <ul className="mx-auto flex max-w-5xl">
          {MAIN.map((l) => {
            const Icon = l.icon;
            const active = path === l.href;
            return (
              <li key={l.href} className="flex-1">
                <Link
                  href={l.href}
                  aria-label={l.label}
                  className={`flex flex-col items-center gap-0.5 py-2 text-[10px] ${
                    active ? "text-emerald-600 dark:text-emerald-400" : "text-faint"
                  }`}
                >
                  <Icon />
                  {l.label}
                </Link>
              </li>
            );
          })}
          <li className="flex-1">
            <button
              onClick={() => setMore((v) => !v)}
              aria-label="Ďalšie"
              aria-expanded={more}
              className={`flex w-full flex-col items-center gap-0.5 py-2 text-[10px] ${
                MORE.some((m) => m.href === path)
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-faint"
              }`}
            >
              <DotsIcon />
              Ďalšie
            </button>
          </li>
        </ul>
        {more && (
          <div className="border-t border-hair px-4 py-2">
            {MORE.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                onClick={() => setMore(false)}
                className="block py-2 text-sm text-muted"
              >
                {l.label}
              </Link>
            ))}
          </div>
        )}
      </nav>
    </>
  );
}

const S = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

function HomeIcon() {
  return <svg {...S}><path d="M3 10.5 12 3l9 7.5" /><path d="M5 10v10h14V10" /></svg>;
}

/** Znak aplikácie — rovnaký ako favicon (app/icon1.tsx): short, kotva, long. */
function BotMark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className="shrink-0">
      <rect x="0.5" y="0.5" width="31" height="31" rx="6.5" fill="#0b0b0e" stroke="#ffffff" strokeOpacity={0.14} />
      <rect x="6" y="6" width="20" height="4" rx="2" fill="#4f9cf5" />
      <rect x="4" y="14" width="24" height="4" rx="2" fill="#f4f4f5" />
      <rect x="6" y="22" width="20" height="4" rx="2" fill="#2fc48d" />
    </svg>
  );
}
function ChartIcon() {
  return <svg {...S}><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></svg>;
}
function ClockIcon() {
  return <svg {...S}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>;
}
function CalendarIcon() {
  return <svg {...S}><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M8 3v4M16 3v4M3 10h18" /></svg>;
}
function MapIcon() {
  return <svg {...S}><path d="M4 20V7l5-3 6 3 5-3v13l-5 3-6-3-5 3Z" /><path d="M9 4v13M15 7v13" /></svg>;
}
function DotsIcon() {
  return <svg {...S}><circle cx="5" cy="12" r="1.2" /><circle cx="12" cy="12" r="1.2" /><circle cx="19" cy="12" r="1.2" /></svg>;
}
