"use client";

import DailyPnlTable from "@/components/DailyPnlTable";
import { DataStamp, RefreshButton, Section, Tile } from "@/components/ui";
import { useLive } from "@/lib/useLive";
import { useEnv } from "@/lib/env";
import { money, pnlClass, signed } from "@/lib/format";
import { cfdTax } from "@/lib/tax";
import type { DailyCycle, Trade } from "@/lib/types";

const pct = (v: number) => `${Math.round(v * 100)} %`;

export default function PnlPage() {
  const { env } = useEnv();
  const { rows: daily, reload: reloadDaily } = useLive<DailyCycle>("daily_cycles", (q) =>
    q.select("*").eq("env", env).order("day", { ascending: false }).limit(400),
    [env],
  );
  const { rows: trades, reload: reloadTrades } = useLive<Trade>("trades", (q) =>
    q.select("*").eq("env", env).order("closed_at", { ascending: false }).limit(500),
    [env],
  );
  const refreshAll = () => { reloadDaily(); reloadTrades(); };

  const newest = trades.reduce<string | null>(
    (a, t) => (!a || (t.closed_at && t.closed_at > a) ? t.closed_at : a),
    null,
  );

  // Daň sa počíta za kalendárny rok zo zatvorených obchodov — daily_cycles
  // nesú čistý výsledok dňa (po províziách a swape), čiže presne základ.
  const year = new Date().getFullYear();
  const ytd = daily
    .filter((d) => d.day.startsWith(`${year}-`))
    .reduce((a, d) => a + (Number(d.pnl_usd) || 0), 0);
  const t = cfdTax(year, ytd);

  return (
    <main>
      <Section
        title={`Dane a odvody ${year}`}
        info={
          <>
            Odhad pre fyzickú osobu. CFD sú ostatný príjem (§ 8 ods. 1 písm. k)
            zákona o dani z príjmov): zdaňuje sa len zisk zo zatvorených pozícií
            za kalendárny rok, provízie sú výdavok. Otvorené pozície sa nezdaňujú —
            ich strata zníži základ, až keď sa zatvoria, a to len v tom istom roku;
            do ďalšieho roka sa strata neprenáša. Zdravotné poistenie {pct(t.healthRate)} sa
            platí v ročnom zúčtovaní, sociálne z týchto príjmov nie. Sadzba dane
            {" "}{pct(t.taxRate)} platí, kým je celý základ dane za rok (vrátane mzdy)
            do 43 983 €; nad ním 25 %, 30 % a 35 % — mzdu treba zadať v lib/tax.ts.
            Oslobodenie 500 € sa neuplatňuje. Nie je to daňové poradenstvo.
          </>
        }
      >
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Tile label="Realizovaný zisk" value={signed(t.profit)} tone={pnlClass(t.profit)} />
          <Tile label={`Daň ${pct(t.taxRate)}`} value={t.tax ? `−${money(t.tax)}` : money(0)} />
          <Tile label={`Zdravotné ${pct(t.healthRate)}`} value={t.health ? `−${money(t.health)}` : money(0)} />
          <Tile label="Čistý po odvodoch" value={signed(t.net)} tone={pnlClass(t.net)}
                sub={t.verified ? undefined : `sadzby pre ${year} neoverené`} />
        </div>
      </Section>

      <Section
        title="P/L"
        info="Celá história, nie posledný mesiac. Riadok vznikne len pre obdobie so zavretým obchodom, podľa dátumu zavretia v našom čase — víkendy a dni bez zavretia teda chýbajú a počet riadkov je nižší než počet kalendárnych dní. Deň sa dá rozkliknúť na jednotlivé zrealizované obchody, týždeň a mesiac na dni, z ktorých sa skladajú. Kumulatív vpravo beží od prvého obchodu, takže posledný riadok hore zodpovedá realizovanému zisku v hlavičke prehľadu."
      >
        <div className="mb-2 flex items-center gap-2">
          <DataStamp iso={newest} />
          <span className="ml-auto"><RefreshButton onClick={refreshAll} /></span>
        </div>
        <DailyPnlTable daily={daily} trades={trades} />
      </Section>
    </main>
  );
}
