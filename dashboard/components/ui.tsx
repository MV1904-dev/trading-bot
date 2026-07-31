"use client";

import { useState } from "react";

/**
 * Stavebné prvky nového rozhrania.
 *
 * Zámer: jeden vizuálny jazyk pre obe témy, žiadne tabuľky (na mobile sa
 * orezávali), dlhé vysvetlivky pod ikonku a všetko orientované po výške.
 */

export function Section({
  title,
  meta,
  info,
  children,
}: {
  title?: string;
  meta?: React.ReactNode;
  info?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="section">
      {(title || meta || info) && (
        <div className="section-head">
          {title && <span>{title}</span>}
          {meta && <span className="text-xs font-normal text-faint">{meta}</span>}
          {info && <InfoButton className="ml-auto">{info}</InfoButton>}
        </div>
      )}
      {children}
    </section>
  );
}

/** Vysvetlivky sa ukážu až na klepnutie — inak zaberali tri riadky pod
    každou sekciou a tlačili obsah pod okraj obrazovky. */
export function InfoButton({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Skryť vysvetlenie" : "Zobraziť vysvetlenie"}
        aria-expanded={open}
        className={`inline-flex h-6 w-6 items-center justify-center rounded-full
          text-faint transition-colors hover:bg-surface hover:text-ink
          ${open ? "bg-surface text-ink" : ""} ${className}`}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 8h.01M11 12h1v4h1" />
        </svg>
      </button>
      {open && (
        <p className="mt-2 w-full basis-full rounded-lg bg-surface px-3 py-2
                      text-xs leading-relaxed text-muted">
          {children}
        </p>
      )}
    </>
  );
}

/** Kompaktná dlaždica pre metriku. */
export function Tile({
  label,
  value,
  tone = "",
  sub,
}: {
  label: string;
  value: string;
  tone?: string;
  sub?: string;
}) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className={`tile-value ${tone}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-faint">{sub}</div>}
    </div>
  );
}

/**
 * Riadok, ktorý sa dá rozkliknúť. Zhrnutie ostáva v jednom riadku bez
 * vodorovného posúvania, zvyšok údajov sa vysunie pod ním.
 */
export function ExpandableRow({
  summary,
  children,
}: {
  summary: React.ReactNode;
  children?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  if (!children) return <div className="row">{summary}</div>;
  return (
    <>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="row text-left hover:bg-surface/60"
      >
        {summary}
        <svg
          width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          strokeLinejoin="round"
          className={`shrink-0 text-faint transition-transform ${open ? "rotate-180" : ""}`}
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div className="mb-1 rounded-lg bg-surface px-3 py-2 text-xs">
          {children}
        </div>
      )}
    </>
  );
}

/** Riadok kľúč — hodnota v rozklikoch. */
export function Detail({
  label,
  value,
  tone = "",
}: {
  label: string;
  value: React.ReactNode;
  tone?: string;
}) {
  return (
    <div className="flex justify-between py-0.5">
      <span className="text-muted">{label}</span>
      <span className={`num ${tone}`}>{value}</span>
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-6 text-center text-sm text-muted">{children}</p>;
}
