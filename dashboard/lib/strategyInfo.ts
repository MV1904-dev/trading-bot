/**
 * Popisy stratégií pre Súťaž — „tak, aby to pochopilo aj malé dieťa"
 * (zadanie 17. 10. 2026). Kľúč je env účtu v Supabase. Nová stratégia
 * bez popisu tu ukáže aspoň krátku poznámku z registra (config.strategy_note).
 */

export type StrategyInfo = {
  idea: string;            // jedna veta: celá stratégia v skratke
  how: string[];           // ako to funguje, krok po kroku
  watch: string;           // na čo si dať pozor
};

/** Spoločný základ všetkých troch gridov — rebrík. */
const LADDER =
  "Predstav si, že cena eura je loptička, ktorá skáče hore a dole. Bot si okolo nej " +
  "postaví rebrík s malými schodíkmi.";

export const STRATEGY_INFO: Record<string, StrategyInfo> = {
  live: {
    idea: `${LADDER} Na každom schodíku kúpi alebo predá kúsok a keď sa loptička kúsok vráti, zoberie si malú odmenu.`,
    how: [
      "Keď cena spadne o jeden schodík (0,10–0,15 %), bot kúpi kúsok za 10 000 €. Keď potom vyskočí o 0,1 % hore, kúsok predá a zostane mu asi 10 € navyše.",
      "Naopak to robí tiež: keď cena vyskočí o schodík, bot „predá dopredu“ (short) a keď spadne o 0,1 %, kúpi späť a zase má asi 10 €.",
      "Robí to na oboch stranách naraz, takže zarába, keď loptička skáče sem a tam.",
      "Schodík sa meria od najvyššieho bodu, kam cena od posledného nákupu došla — preto pri malom kmitaní nakupuje aj bližšie za sebou.",
      "Nikdy nepredá so stratou. Keď cena ide proti nemu, kúsok si nechá a trpezlivo čaká, kým sa vráti.",
      "Pod 1,12 už nepredáva dopredu a nad 1,16 už nekupuje. Na každej strane má najviac 30 kúskov.",
      "Ako veľké sú schodíky, rozhoduje cena za noc (swap): čím je držanie drahšie, tým väčší schodík.",
    ],
    watch: "Keď loptička dlho padá alebo stúpa jedným smerom, bot nazbiera veľa kúskov v mínuse a čaká. Za držanie kúpených kúskov sa každú noc platí malý poplatok a ak by strata narástla príliš, broker kúsky zatvorí sám (pri marži 50 %).",
  },
  paper: {
    idea: `${LADDER} Strážca sa ale navyše pozerá na „počasie“ — či cena dlhšie ide hore alebo dole — a proti silnému vetru veľa nenakupuje.`,
    how: [
      "Funguje ako obyčajný rebrík: kúsok kúpi pri poklese, predá pri raste o 0,1 % a zoberie si asi 10 €.",
      "Počasie zistí tak, že porovná priemernú cenu za posledné ~4 dni s priemerom za ~17 dní. Keď je kratší priemer vyššie, fúka vietor hore, keď nižšie, fúka dole.",
      "V smere vetra pracuje naplno: schodík 0,10 % a najviac 20 kúskov.",
      "Proti vetru je opatrný: schodík je dvakrát väčší (0,20 %) a najviac 4 kúsky. Takže keď cena dlho padá, nenahromadí si kopu kúskov v mínuse.",
      "Má aj vlastnú poistku: keď by marža klesla pod 110 %, sám zavrie najhorší kúsok — skôr, než by to pri 50 % urobil broker.",
      "Rovnako ako live: pod 1,12 nepredáva dopredu, nad 1,16 nekupuje.",
    ],
    watch: "Keď sa vietor často otáča, Strážca môže byť chvíľu „na zlej strane“ a zarobí menej ako obyčajný grid. Výmenou za to by pri dlhom páde mal mať oveľa menšiu stratu.",
  },
  "paper-grid": {
    idea: `${LADDER} Toto je presná kópia live gridu, len na virtuálnom účte, ktorý začal prázdny.`,
    how: [
      "Robí úplne to isté, čo live: rovnaké schodíky, rovnaké pásmo 1,12–1,16, najviac 30 kúskov na stranu, rovnaká zmena schodíkov podľa swapu.",
      "Rozdiel je len v tom, že začal s čistým účtom — nemá staré kúsky, ktoré live nazbieral pred súťažou.",
      "Slúži ako meradlo: ukazuje, ako by sa darilo pravidlám live gridu, keby začínali dnes.",
      "Keď ho porovnáš s „Grid + dobeh“, vidíš presne, čo pridá samotný dobeh — všetko ostatné majú rovnaké.",
    ],
    watch: "Je to merací prútik, nie nová stratégia. Ak sa od live líši, je to preto, že live si nesie staré pozície a má skutočné plnenie u brokera.",
  },
  "paper-dobeh": {
    idea: `${LADDER} Keď cena preskočí naraz viac schodíkov, tento grid si všetky zmeškané schodíky dobehne naraz.`,
    how: [
      "Väčšinu času robí to isté ako kópia live gridu.",
      "Niekedy však cena skočí o viac schodíkov naraz — napríklad pri dôležitej správe, keď bot pol hodinu čaká a nenakupuje (blackout).",
      "Obyčajný grid by potom kúpil len jeden kúsok a zvyšné schodíky preskočil.",
      "Tento kúpi toľko kúskov, koľko schodíkov cena preskočila (najviac 6) — a všetky za tú lepšiu cenu, ktorú cena po skoku ponúka.",
      "Každý z nich predá pri návrate o 0,1 %. Keď sa cena vráti, zoberie naraz napríklad 4 × 10 € namiesto 10 €.",
      "Ráta len schodíky vnútri pásma 1,12–1,16 — keď sa cena vráti do pásma zvonku, nedobieha nič.",
    ],
    watch: "Keď cena po skoku pokračuje ďalej rovnakým smerom, má v mínuse viac kúskov naraz. Či sa dobeh oplatí, ukáže práve táto súťaž.",
  },
};
