"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import ThemeToggle from "@/components/ThemeToggle";

const LINKS = [
  { href: "/", label: "Prehľad" },
  { href: "/strategies", label: "Stratégie" },
  { href: "/positions", label: "Pozície" },
  { href: "/history", label: "História" },
  { href: "/calendar", label: "Kalendár" },
];

export default function Nav() {
  const path = usePathname();
  if (path?.startsWith("/login")) return null;

  return (
    <>
      {/* Horná lišta: na mobile nesie len prepínač témy (odkazy sú dole),
          na desktope nesie oboje. */}
      <header className="mx-auto flex w-full max-w-5xl items-center gap-1 px-4 pt-3">
        <ul className="hidden flex-1 sm:flex">
          {LINKS.map((l) => (
            <li key={l.href}>
              <NavLink {...l} active={path === l.href} />
            </li>
          ))}
        </ul>
        <span className="flex-1 text-sm font-semibold sm:hidden">Trading bot</span>
        <ThemeToggle />
      </header>

      <nav
        className="bottom-nav fixed inset-x-0 bottom-0 z-20 border-t border-line
                   bg-bg/95 backdrop-blur sm:hidden"
      >
        <ul className="mx-auto flex max-w-5xl">
          {LINKS.map((l) => (
            <li key={l.href} className="flex-1">
              <NavLink {...l} active={path === l.href} center />
            </li>
          ))}
        </ul>
      </nav>
    </>
  );
}

function NavLink({
  href,
  label,
  active,
  center,
}: {
  href: string;
  label: string;
  active: boolean;
  center?: boolean;
}) {
  return (
    <Link
      href={href}
      className={`block px-4 py-3 text-sm ${center ? "text-center" : ""} ${
        active ? "text-emerald-600 dark:text-emerald-400" : "text-muted hover:text-ink"
      }`}
    >
      {label}
    </Link>
  );
}
