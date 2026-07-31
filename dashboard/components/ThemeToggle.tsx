"use client";

import { useEffect, useState } from "react";

type Mode = "light" | "dark";

/**
 * Prepínač témy. Default je systémové nastavenie; po prvom kliknutí sa
 * voľba uloží do localStorage a systém sa už neberie do úvahy.
 *
 * Samotné nastavenie triedy pri štarte robí inline skript v layoute —
 * keby to robil až tento komponent, stránka by na okamih blikla v zlej
 * téme, kým sa načíta React.
 */
export default function ThemeToggle() {
  const [mode, setMode] = useState<Mode | null>(null);

  useEffect(() => {
    setMode(document.documentElement.classList.contains("dark") ? "dark" : "light");
  }, []);

  function toggle() {
    const next: Mode = mode === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", next === "dark");
    localStorage.setItem("theme", next);
    setMode(next);
  }

  // Kým nevieme aktuálny stav, renderujeme prázdne miesto rovnakej
  // veľkosti — inak by tlačidlo po hydratácii poskočilo.
  if (mode === null) return <span className="h-9 w-9" aria-hidden />;

  return (
    <button
      onClick={toggle}
      aria-label={mode === "dark" ? "Prepnúť na svetlý režim" : "Prepnúť na tmavý režim"}
      title={mode === "dark" ? "Svetlý režim" : "Tmavý režim"}
      className="inline-flex h-9 w-9 items-center justify-center rounded-lg
                 text-muted transition-colors hover:bg-line/40 hover:text-ink"
    >
      {mode === "dark" ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

function SunIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="2" strokeLinecap="round"
         strokeLinejoin="round">
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
    </svg>
  );
}
