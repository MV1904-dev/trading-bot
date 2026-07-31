"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Prehľad" },
  { href: "/strategies", label: "Stratégie" },
  { href: "/positions", label: "Pozície" },
  { href: "/history", label: "História" },
];

export default function Nav() {
  const path = usePathname();
  if (path?.startsWith("/login")) return null;

  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-20 border-t border-zinc-800
                 bg-zinc-950/95 backdrop-blur sm:static sm:mx-auto sm:mt-0
                 sm:max-w-5xl sm:border-0 sm:bg-transparent"
    >
      <ul className="mx-auto flex max-w-5xl">
        {LINKS.map((l) => {
          const active = path === l.href;
          return (
            <li key={l.href} className="flex-1 sm:flex-none">
              <Link
                href={l.href}
                className={`block px-4 py-3 text-center text-sm sm:text-left
                  ${active ? "text-emerald-400" : "text-zinc-400 hover:text-zinc-200"}`}
              >
                {l.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
