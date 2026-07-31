"use client";

import { money, signed } from "@/lib/format";
import type { BotState, Position } from "@/lib/types";

/** Margin level, pod ktorým broker začne zatvárať pozície. */
const STOP_OUT = 50;
const WARN = 200;
const DANGER = 100;

/**
 * Dve výstrahy, ktoré si vyžiadal: potreba kapitálu (marža) a náklad
 * držania (swap). Sú to dve rôzne riziká — marža ťa vyhodí z trhu naraz,
 * swap ojedá pozíciu pomaly.
 */
export default function Alerts({
  state,
  positions,
}: {
  state: BotState | null;
  positions: Position[];
}) {
  if (!state) return null;
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <MarginCard state={state} positions={positions} />
      <SwapCard state={state} positions={positions} />
    </div>
  );
}

function MarginCard({
  state,
  positions,
}: {
  state: BotState;
  positions: Position[];
}) {
  const level = state.margin_level;
  const used = state.used_margin ?? 0;
  const free = state.free_margin ?? 0;
  const equity = state.equity ?? state.balance ?? 0;

  // Koľko ďalších pozícií rovnakej veľkosti účet utiahne. Marža na
  // pozíciu berieme z priemeru otvorených — presnejšie než počítať
  // z páky, lebo tú broker môže mať odstupňovanú podľa objemu.
  const perPosition = positions.length ? used / positions.length : 0;
  const room = perPosition > 0 ? Math.floor(free / perPosition) : null;

  const tone =
    level == null
      ? "ok"
      : level < DANGER
        ? "danger"
        : level < WARN
          ? "warn"
          : "ok";

  return (
    <div
      className={`card ${
        tone === "danger"
          ? "border-neg"
          : tone === "warn"
            ? "border-warn"
            : ""
      }`}
    >
      <div className="flex items-baseline justify-between">
        <span className="card-title">Marža / potreba kapitálu</span>
        <span
          className={`font-mono text-lg ${
            tone === "danger"
              ? "text-neg"
              : tone === "warn"
                ? "text-warn"
                : "text-ink"
          }`}
        >
          {level == null ? "—" : `${level.toFixed(0)} %`}
        </span>
      </div>

      <div className="mt-3 h-2 overflow-hidden rounded-full bg-line">
        <div
          className={
            tone === "danger"
              ? "h-full bg-neg"
              : tone === "warn"
                ? "h-full bg-warn"
                : "h-full bg-emerald-600"
          }
          style={{ width: `${equity > 0 ? Math.min((used / equity) * 100, 100) : 0}%` }}
        />
      </div>

      <dl className="mt-3 grid grid-cols-3 gap-2 text-sm">
        <div>
          <dt className="card-title">Využitá</dt>
          <dd className="mt-0.5 font-mono">{money(used)}</dd>
        </div>
        <div>
          <dt className="card-title">Voľná</dt>
          <dd className="mt-0.5 font-mono">{money(free)}</dd>
        </div>
        <div>
          <dt className="card-title">Ešte pozícií</dt>
          <dd className="mt-0.5 font-mono">{room ?? "—"}</dd>
        </div>
      </dl>

      <p className="mt-3 text-xs text-faint">
        {tone === "danger" ? (
          <span className="text-neg">
            Margin level pod {DANGER} %. Pri {STOP_OUT} % začne broker zatvárať
            pozície sám — dolej kapitál alebo zavri časť mriežky.
          </span>
        ) : tone === "warn" ? (
          <span className="text-warn">
            Margin level pod {WARN} %. Ešte je priestor, ale ďalší pohyb proti
            mriežke ho zje rýchlo.
          </span>
        ) : (
          <>
            Stop-out je pri {STOP_OUT} %. „Ešte pozícií" je odhad z priemernej
            marže otvorených pozícií, nie záväzný limit.
          </>
        )}
      </p>
    </div>
  );
}

function SwapCard({
  state,
  positions,
}: {
  state: BotState;
  positions: Position[];
}) {
  const sl = state.swap_long;
  const ss = state.swap_short;
  const ratio =
    sl != null && ss != null && ss !== 0 ? Math.abs(sl / ss) : null;

  const longs = positions.filter((p) => p.side === "long");
  const shorts = positions.filter((p) => p.side === "short");
  const accrued = positions.reduce((a, p) => a + (Number(p.funding_usd) || 0), 0);

  // Streda = trojitý swap (víkendový rollover sa účtuje dopredu).
  const triple = state.swap_rollover_3days;
  const DAYS = ["nedeľa", "pondelok", "utorok", "streda", "štvrtok", "piatok", "sobota"];
  const today = new Date().getDay();
  const isTripleToday = triple != null && today === triple;

  return (
    <div className={`card ${isTripleToday && longs.length ? "border-warn" : ""}`}>
      <div className="flex items-baseline justify-between">
        <span className="card-title">Swap / náklad držania</span>
        <span className="font-mono text-lg">{signed(accrued)}</span>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
        <div>
          <dt className="card-title">Sadzba long</dt>
          <dd className="mt-0.5 font-mono text-long">{sl ?? "—"}</dd>
        </div>
        <div>
          <dt className="card-title">Sadzba short</dt>
          <dd className="mt-0.5 font-mono text-short">{ss ?? "—"}</dd>
        </div>
      </dl>

      {ratio != null && ratio > 1.5 && (
        <p className="mt-3 text-sm">
          Long platí <strong>{ratio.toFixed(0)}×</strong> viac swapu než short.
          {longs.length > 0 ? (
            <>
              {" "}
              Otvorených longov: <strong>{longs.length}</strong> — držanie cez
              noc ich stojí neúmerne viac.
            </>
          ) : (
            <> Teraz nemáš otvorený žiadny long, takže sa ťa to netýka.</>
          )}
        </p>
      )}

      {triple != null && (
        <p
          className={`mt-2 text-xs ${
            isTripleToday && longs.length ? "text-warn" : "text-faint"
          }`}
        >
          Trojitý swap sa účtuje v {DAYS[triple]}
          {isTripleToday ? " — teda dnes." : "."}{" "}
          {shorts.length > 0 && `Otvorených shortov: ${shorts.length}.`}
        </p>
      )}

      <p className="mt-2 text-xs text-faint">
        Sadzby sú v jednotkách brokera; ich pomer je vypovedajúci, absolútny
        náklad za noc čítaj z už naúčtovaného swapu vyššie.
      </p>
    </div>
  );
}
