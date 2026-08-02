"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import ThemeToggle from "@/components/ThemeToggle";

/** Štyri hlavné položky sa vojdú bez orezania popisku; Stratégie sú
    statickejšia sekcia, tak idú pod „ďalšie". */
const MAIN = [
  { href: "/", label: "Prehľad", icon: HomeIcon },
  { href: "/plan", label: "Plán", icon: PlanIcon },
  { href: "/positions", label: "Pozície", icon: LayersIcon },
  { href: "/calendar", label: "Kalendár", icon: CalendarIcon },
];
// História a Stratégie sa prezerajú, nevyžadujú dennú akciu — plán áno,
// preto dostal hlavný slot.
const MORE = [
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
          <span className="flex-1 text-sm font-medium sm:hidden">
            {all.find((l) => l.href === path)?.label ?? "Trading bot"}
          </span>
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
function LayersIcon() {
  return <svg {...S}><path d="M12 3 3 8l9 5 9-5-9-5Z" /><path d="m3 14 9 5 9-5" /></svg>;
}
function ClockIcon() {
  return <svg {...S}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>;
}
function CalendarIcon() {
  return <svg {...S}><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M8 3v4M16 3v4M3 10h18" /></svg>;
}
function PlanIcon() {
  return <svg {...S}><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h4" /></svg>;
}
function DotsIcon() {
  return <svg {...S}><circle cx="5" cy="12" r="1.2" /><circle cx="12" cy="12" r="1.2" /><circle cx="19" cy="12" r="1.2" /></svg>;
}
