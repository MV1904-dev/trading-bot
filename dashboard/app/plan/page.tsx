"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase";
import { useLive } from "@/lib/useLive";
import { dateTime, money, price as fmtPrice } from "@/lib/format";
import { Detail, Empty, ExpandableRow, Section } from "@/components/ui";
import type { DailyPlan, Scenario } from "@/lib/types";

const STATUS = {
  pending: { text: "Čaká na schválenie", cls: "bg-warn/15 text-warn" },
  approved: { text: "Schválený", cls: "bg-longbg text-long" },
  rejected: { text: "Zamietnutý", cls: "bg-neg/15 text-neg" },
  expired: { text: "Prepadnutý", cls: "bg-surface text-muted" },
} as const;

export default function PlanPage() {
  const { rows, reload } = useLive<DailyPlan>("daily_plans", (q) =>
    q.select("*").order("plan_date", { ascending: false }).limit(10),
  );
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<"approved" | "rejected" | null>(null);

  const plan = rows[0] ?? null;
  if (!plan) {
    return (
      <main>
        <Section title="Denný plán">
          <Empty>Zatiaľ nie je vygenerovaný žiadny plán.</Empty>
        </Section>
      </main>
    );
  }

  const st = STATUS[plan.status];
  const ctx = plan.context;
  const tradable = plan.scenarios.filter((s) => s.side !== null);
  const noTrade = plan.scenarios.find((s) => s.tag === "A2");

  async function decide(status: "approved" | "rejected") {
    setBusy(true);
    const supabase = createClient();
    const { data: { user } } = await supabase.auth.getUser();
    const { error } = await supabase
      .from("daily_plans")
      .update({
        status,
        approved_at: new Date().toISOString(),
        approved_by: user?.id,
      })
      .eq("plan_date", plan!.plan_date)
      .eq("status", "pending");
    setBusy(false);
    setConfirming(null);
    setMsg(
      error
        ? `Nepodarilo sa: ${error.message}`
        : status === "approved"
          ? "Plán schválený — bot ho môže vykonať."
          : "Plán zamietnutý, dnes sa neobchoduje.",
    );
    reload();
  }

  return (
    <main>
      <Section>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`chip ${st.cls}`}>{st.text}</span>
          <span className="text-sm font-medium">{plan.plan_date}</span>
          <span className="text-xs text-faint">{plan.symbol}</span>
        </div>
        <p className="mt-2 text-xs text-faint">
          Zostavené {dateTime(plan.generated_at)} pri {fmtPrice(plan.price_at_build)},
          ATR {plan.atr_d1_pips} p, equity {money(plan.equity)} €.
        </p>
        {plan.data_source && (
          <p className="mt-0.5 text-xs text-faint">{plan.data_source}</p>
        )}
        {plan.stale_warning && (
          <p className="mt-2 rounded-lg bg-neg/10 px-3 py-2 text-xs text-neg">
            {plan.stale_warning}
          </p>
        )}
        {plan.approved_at && (
          <p className="mt-2 text-xs text-muted">
            Rozhodnuté {dateTime(plan.approved_at)}
          </p>
        )}
        {msg && <p className="mt-2 text-xs text-muted">{msg}</p>}
      </Section>

      {plan.narrative && (
        <Section
          title="Ako o tom uvažujem"
          info="Text sa generuje z tých istých dát ako plán — popisuje skutočnú úvahu enginu a nemôže sa od čísel rozísť."
        >
          <p className="whitespace-pre-line text-sm leading-relaxed text-muted">
            {plan.narrative}
          </p>
        </Section>
      )}

      <Section
        title="Prečo"
        info="L1 makro je zatiaľ len úrokový diferenciál ECB−Fed a jeho týždenná zmena; 2Y výnosy US−DE nie sú napojené. L5 správy nie sú napojené vôbec. Bias teda stojí na L1 a L2."
      >
        <div className="row">
          <span className="w-10 shrink-0 text-xs text-faint">L1</span>
          <span className="text-sm">
            <strong>{ctx.L1_macro.bias}</strong> — {ctx.L1_macro.reason}
          </span>
        </div>
        <div className="row">
          <span className="w-10 shrink-0 text-xs text-faint">L2</span>
          <span className="text-sm">
            <strong>{ctx.L2_structure.bias}</strong>, cena na{" "}
            {ctx.L2_structure.location} → logika {ctx.L2_structure.logic}
            {ctx.L2_structure.pct_1y != null && (
              <> · percentil 1R {ctx.L2_structure.pct_1y.toFixed(0)} %</>
            )}
          </span>
        </div>
        <div className="row">
          <span className="w-10 shrink-0 text-xs text-faint">L4</span>
          <span className="text-sm">
            {ctx.L4_events.length === 0
              ? "žiadne udalosti v horizonte 3 dní"
              : ctx.L4_events
                  .slice(0, 3)
                  .map((e) => `T${e.tier} ${e.ts.slice(11, 16)} ${e.title}`)
                  .join(" · ")}
          </span>
        </div>
        <div className="row">
          <span className="w-10 shrink-0 text-xs text-faint">L5</span>
          <span className="text-sm text-muted">
            {ctx.L5_news.length ? ctx.L5_news.map((n) => n.text).join("; ")
                                : "správy nenapojené"}
          </span>
        </div>
      </Section>

      <Section title="Úrovne" meta="sila = počet konfluencií">
        {ctx.L3_resistance.slice(0, 3).map((z, i) => (
          <div key={`r${i}`} className="row">
            <span className="chip-short">R</span>
            <span className="num text-sm">
              {z.low.toFixed(5)}–{z.high.toFixed(5)}
            </span>
            <span className="text-xs text-faint">sila {z.strength}</span>
            <span className="ml-auto truncate text-xs text-faint">
              {z.sources.slice(0, 2).join(", ")}
            </span>
          </div>
        ))}
        {ctx.L3_support.slice(0, 3).map((z, i) => (
          <div key={`s${i}`} className="row">
            <span className="chip-long">S</span>
            <span className="num text-sm">
              {z.low.toFixed(5)}–{z.high.toFixed(5)}
            </span>
            <span className="text-xs text-faint">sila {z.strength}</span>
            <span className="ml-auto truncate text-xs text-faint">
              {z.sources.slice(0, 2).join(", ")}
            </span>
          </div>
        ))}
      </Section>

      <Section
        title="Návrh pozícií"
        meta="P a A1 sú OCO"
        info="Aktivácia jedného scenára ruší druhý. SL je minimálne 0,8×ATR od vstupu a nikdy sa nepribližuje. TP1 zavrie polovicu, zvyšok beží na TP2. Time stop sú 3 obchodné dni."
      >
        {tradable.length === 0 ? (
          <Empty>Dnes žiadny obchodovateľný scenár.</Empty>
        ) : (
          tradable.map((s) => <ScenarioRow key={s.tag} s={s} />)
        )}
      </Section>

      {noTrade && noTrade.invalidation.length > 0 && (
        <Section title="Kedy sa neobchoduje">
          {noTrade.invalidation.map((x, i) => (
            <div key={i} className="row">
              <span className="text-sm text-muted">{x}</span>
            </div>
          ))}
        </Section>
      )}

      {plan.status === "pending" && (
        <Section>
          <div className="flex gap-2">
            <button
              onClick={() => setConfirming("rejected")}
              disabled={busy}
              className="btn-ghost flex-1"
            >
              Zamietnuť
            </button>
            <button
              onClick={() => setConfirming("approved")}
              disabled={busy || !!plan.stale_warning}
              className="btn-primary flex-1"
            >
              Súhlasím
            </button>
          </div>
          {plan.stale_warning && (
            <p className="mt-2 text-xs text-neg">
              Schválenie je zablokované, kým sú dáta zastarané.
            </p>
          )}
          <p className="mt-2 text-xs text-faint">
            Bez schválenia bot nevstúpi. Schválený plán sa už nedá zmeniť —
            meniť sa smie len jeho stav.
          </p>
        </Section>
      )}

      {confirming && (
        <div
          className="fixed inset-0 z-30 flex items-end justify-center bg-black/60 p-4 sm:items-center"
          onClick={() => setConfirming(null)}
        >
          <div
            className="w-full max-w-sm rounded-xl bg-solid p-4 shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="font-medium">
              {confirming === "approved" ? "Nasadiť plán do reality?" : "Zamietnuť plán?"}
            </h2>
            {confirming === "approved" ? (
              <div className="mt-2 space-y-1 text-sm text-muted">
                {tradable.map((s) => (
                  <p key={s.tag}>
                    <strong>{s.tag}</strong> {s.side === "buy" ? "LONG" : "SHORT"}{" "}
                    {s.volume ? money(s.volume, 0) : "—"} @{" "}
                    {s.entry_lo?.toFixed(5)}–{s.entry_hi?.toFixed(5)}, riziko{" "}
                    {s.risk_eur ? `${money(s.risk_eur)} €` : "—"}
                  </p>
                ))}
                <p className="pt-1 text-xs text-faint">
                  Bot bude čakať na trigger a vstúpi sám. Zásah do plánu už
                  nebude možný okrem news guardu.
                </p>
              </div>
            ) : (
              <p className="mt-2 text-sm text-muted">
                Dnes sa nebude obchodovať. Plán ostane v denníku ako zamietnutý.
              </p>
            )}
            <div className="mt-4 flex gap-2">
              <button onClick={() => setConfirming(null)} className="btn-ghost flex-1">
                Späť
              </button>
              <button
                onClick={() => decide(confirming)}
                disabled={busy}
                className={confirming === "approved" ? "btn-primary flex-1" : "btn-danger flex-1"}
              >
                {busy ? "Odosielam…" : confirming === "approved" ? "Súhlasím" : "Zamietnuť"}
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

function ScenarioRow({ s }: { s: Scenario }) {
  return (
    <ExpandableRow
      summary={
        <>
          <span className="chip bg-surface text-muted">{s.tag}</span>
          <span className={s.side === "buy" ? "chip-long" : "chip-short"}>
            {s.side === "buy" ? "LONG" : "SHORT"}
          </span>
          <span className="text-xs text-faint">{s.kind}</span>
          <span className="num ml-auto text-sm">
            {s.entry_lo?.toFixed(5)}–{s.entry_hi?.toFixed(5)}
          </span>
        </>
      }
    >
      <Detail label="Trigger" value={s.trigger} />
      <Detail label="Objem" value={s.volume ? money(s.volume, 0) : "—"} />
      <Detail label="Riziko" value={s.risk_eur ? `${money(s.risk_eur)} €` : "—"} />
      <Detail label="Stop loss" value={s.sl?.toFixed(5) ?? "—"} tone="text-neg" />
      <Detail
        label="TP1"
        value={s.tp1 ? `${s.tp1.toFixed(5)} (RR ${s.rr1?.toFixed(1)})` : "—"}
        tone="text-pos"
      />
      <Detail
        label="TP2"
        value={s.tp2 ? `${s.tp2.toFixed(5)} (RR ${s.rr2?.toFixed(1)})` : "—"}
        tone="text-pos"
      />
      <Detail label="Time stop" value={`${s.time_stop_days} obchodné dni`} />
      {s.note && <p className="mt-2 text-xs text-faint">{s.note}</p>}
      {s.invalidation.length > 0 && (
        <div className="mt-2">
          <p className="text-xs text-faint">Invalidácia:</p>
          {s.invalidation.map((x, i) => (
            <p key={i} className="text-xs text-muted">
              · {x}
            </p>
          ))}
        </div>
      )}
    </ExpandableRow>
  );
}
