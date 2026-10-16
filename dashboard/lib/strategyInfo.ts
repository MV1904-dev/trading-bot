/**
 * Pravidlá stratégií pre Súťaž — „čo kedy bot urobí a prečo" (zadanie
 * 17. 10. 2026). Každé pravidlo zodpovedá kódu:
 *   live, paper-grid, paper-dobeh — trading/strategy_grid25.py (kotva na
 *     bežiacom extréme), swapové kroky bot_ctrader._apply_swap_regime,
 *     dobeh trading/strategy_grid_virtual.py
 *   paper (Strážca) — trading/strategy_guard.py
 * Kľúč je env účtu v Supabase. Pri zmene stratégie treba zmeniť aj text.
 */

export type Rule = { when: string; what: string; why: string };

export type StrategyInfo = {
  summary: string;
  rules: Rule[];
  risk: string;
};

const CHECK: Rule = {
  when: "Každých 10 sekúnd a na konci každej 5-minútovej sviečky",
  what: "Prečíta kurz. O nových vstupoch rozhoduje len na konci 5-minútovej sviečky, podľa jej zatváracej ceny.",
  why: "Jedno rozhodnutie za 5 minút bráni tomu, aby jeden rýchly výkyv otvoril viac pozícií naraz.",
};

const tp = (gap: string): Rule => ({
  when: "Hneď pri otvorení pozície",
  what: `Položí u brokera príkaz na zatvorenie so ziskom (TP) o 0,1 % ďalej — pri 10 000 € je to ≈ 10 € hrubého zisku. ${gap}`,
  why: "TP leží u brokera, takže sa zatvorí aj vtedy, keď bot práve nebeží.",
});
const TP = tp("Ak kurz pri vstupe preskočil 2 a viac krokov, TP je o jeden krok (0,15 % long, 0,10 % short).");

const NO_SL: Rule = {
  when: "Pozícia je v strate",
  what: "Nezatvára ju. Drží ju, kým kurz nedôjde k TP.",
  why: "Stratégia stavia na tom, že EURUSD sa v pásme 1,12–1,16 väčšinou vracia. Stop-loss by stratu zrealizoval skôr, než sa kurz vráti.",
};

const BLACKOUT: Rule = {
  when: "30 minút pred a 30 minút po dôležitej správe pre USD alebo EUR (kalendár ForexFactory, vysoký dopad)",
  what: "Neotvára žiadne nové pozície. Otvorené pozície a ich TP ostávajú.",
  why: "Pri správach kurz skáče a spread sa rozširuje — vstup by padol na náhodnej cene.",
};

const BROKER: Rule = {
  when: "Úroveň marže klesne na 50 %",
  what: "Broker sám zatvára pozície od najstratovejšej, kým marža nie je znova nad 50 % (stop-out).",
  why: "Pravidlo brokera, nie bota — chráni účet pred záporným zostatkom.",
};

/** Pravidlá live gridu — rovnaké pre live, jeho kópiu aj variant s dobehom. */
const GRID_RULES: Rule[] = [
  CHECK,
  {
    when: "Kurz klesne o krok pod najvyššiu cenu od posledného longu (dnes krok 0,15 %) a zároveň aspoň o 2× ATR",
    what: "Kúpi 10 000 € (long) — ak je kurz pod 1,16 a otvorených longov je menej ako 30. Nad 20 longov len vtedy, ak je kurz od posledného longu ďalej než 2× ATR.",
    why: "Kupuje lacnejšie po poklese a čaká na odraz. ATR (priemerný rozsah 5-min sviečky) zabráni vstupom, keď je trh príliš rozkolísaný na daný krok.",
  },
  {
    when: "Kurz stúpne o krok nad najnižšiu cenu od posledného shortu (dnes krok 0,10 %)",
    what: "Predá 10 000 € (short) — ak je kurz nad 1,12 a otvorených shortov je menej ako 30 (nad 20 len pri odstupe > 2× ATR).",
    why: "Zrkadlo longu: predáva drahšie po raste a čaká na pokles. Obe strany bežia naraz.",
  },
  {
    when: "Po každom vstupe",
    what: "Ďalší krok počíta od ceny tohto vstupu a odtiaľ znova sleduje najvyšší (long) / najnižší (short) kurz.",
    why: "Pri kmitaní tak môže ďalší vstup prísť aj bližšie než o celý krok od predošlého — preto má live pozície niekedy len 4–6 pipov od seba.",
  },
  TP,
  NO_SL,
  {
    when: "Pri každej zmene swapových sadzieb u brokera",
    what: "Prepočíta, koľko stojí strana za noc pri 10 000 €: do 0,25 € → krok 0,10 %, do 0,50 € → 0,13 %, do 1,00 € → 0,15 %, viac → strana sa neotvára. Dnes long platí ~0,65 €/noc → 0,15 %, short dostáva → 0,10 %.",
    why: "Drahšie držanie = redšie vstupy, aby pozície nečakali v strate dlho za vysoký poplatok.",
  },
  BLACKOUT,
  BROKER,
];

export const STRATEGY_INFO: Record<string, StrategyInfo> = {
  live: {
    summary: "Obojstranný grid na skutočnom účte: kupuje po poklese, predáva po raste, každú pozíciu zatvára so ziskom ~10 € a stratové drží bez stop-lossu.",
    rules: GRID_RULES,
    risk: "Keď kurz ide dlho jedným smerom, pribúdajú pozície v strate (až 30) a čakajú — longy pritom každú noc platia swap. Live si nesie aj pozície otvorené pred súťažou.",
  },
  paper: {
    summary: "Ten istý grid, ale podľa trendu obmedzuje stranu, ktorá ide proti nemu: proti trendu najviac 4 pozície s dvojnásobným krokom.",
    rules: [
      CHECK,
      {
        when: "Na konci každej hodiny",
        what: "Spočíta kĺzavý priemer (EMA) hodinových zatváracích cien za 100 hodín (~4 dni) a za 400 hodín (~17 dní). Keď je 100-hodinový vyššie, trend je HORE, inak DOLE.",
        why: "Dlhšie priemery nereagujú na krátke výkyvy — ukazujú, kam trh ide už niekoľko dní.",
      },
      {
        when: "Kurz klesne pod najvyššiu cenu od posledného longu o 0,10 % (trend HORE) alebo o 0,20 % (trend DOLE), a aspoň o 2× ATR",
        what: "Kúpi 10 000 € — ak je kurz pod 1,16 a longov je menej ako 20 (trend HORE) alebo menej ako 4 (trend DOLE).",
        why: "V smere trendu nakupuje hustejšie, proti nemu len málo a riedko — aby sa pri dlhom páde nenahromadila kopa stratových longov.",
      },
      {
        when: "Kurz stúpne nad najnižšiu cenu od posledného shortu o 0,10 % (trend DOLE) alebo o 0,20 % (trend HORE)",
        what: "Predá 10 000 € — ak je kurz nad 1,12 a shortov je menej ako 20 (trend DOLE) alebo menej ako 4 (trend HORE).",
        why: "Zrkadlo longov: shorty naplno, keď trh klesá, a opatrne, keď rastie.",
      },
      {
        when: "Trend sa otočí",
        what: "Otvorené pozície nechá s ich TP. Mení sa len to, koľko nových pozícií a s akým krokom smie otvoriť.",
        why: "Zatvárať pri každom otočení by znamenalo realizovať straty pri každom falošnom signále.",
      },
      tp("Ak kurz pri vstupe preskočil 2 a viac krokov, TP je o jeden krok (0,10 % v smere trendu, 0,20 % proti nemu)."),
      {
        when: "Pozícia je v strate",
        what: "Nezatvára ju, drží ju do TP — s jednou výnimkou nižšie.",
        why: "Rovnako ako live grid stavia na návrate kurzu v pásme.",
      },
      {
        when: "Úroveň marže klesne pod 110 %",
        what: "Zatvorí pozíciu s najväčšou stratou, a ak treba, ďalšiu, kým marža nie je znova nad 110 %.",
        why: "Vlastná poistka: zatvorí najhoršie pozície skôr, než by to pri 50 % urobil broker plošne.",
      },
      BLACKOUT,
      BROKER,
    ],
    risk: "Keď sa trend často otáča, býva na „zlej“ strane a zarobí menej než obyčajný grid. Výmenou by pri dlhom pohybe jedným smerom mal mať oveľa menšiu stratu (lab: max prepad 7,7k oproti 12,6k).",
  },
  "paper-grid": {
    summary: "Presná kópia live gridu na virtuálnom účte, ktorý začal prázdny — meradlo pre ostatné varianty.",
    rules: [
      ...GRID_RULES,
      {
        when: "Štart súťaže",
        what: "Začal bez pozícií s 15 200 €. Kurz a swapové sadzby dostáva zo živého bota; plnenia, provízie a swap sa simulujú podľa live účtu.",
        why: "Ukazuje, ako by dopadli pravidlá live gridu bez starých pozícií. Rozdiel oproti „Grid + dobeh“ je čistý efekt dobehu.",
      },
    ],
    risk: "Rovnaké ako live grid. Odchýlka od live je spôsobená starými pozíciami live účtu a skutočným plnením u brokera.",
  },
  "paper-dobeh": {
    summary: "Kópia live gridu s jedným pravidlom navyše: keď kurz preskočí naraz viac krokov, otvorí všetky zmeškané pozície naraz.",
    rules: [
      ...GRID_RULES.slice(0, 3),
      {
        when: "Pri vstupe kurz prešiel od kotvy naraz 2 a viac krokov (napr. po blackoute alebo prudkom pohybe)",
        what: "Namiesto jednej otvorí toľko pozícií po 10 000 €, koľko krokov kurz preskočil — najviac 6 a najviac do stropu 30. Všetky majú TP +0,1 % od aktuálneho kurzu. Rátajú sa len kroky vnútri pásma 1,12–1,16.",
        why: "Postupný grid by na tých krokoch otvoril pozície tiež, len za horšie ceny. Takto má tú istú expozíciu za lepšiu cenu. Kroky mimo pásma sa nedobiehajú, lebo tie grid vynechal zámerne.",
      },
      ...GRID_RULES.slice(3).map((r) => (r === TP
        ? tp("Aj pri dobehu majú všetky pozície TP +0,1 % od aktuálneho kurzu.") : r)),
      {
        when: "Štart súťaže",
        what: "Začal bez pozícií s 15 200 €, v rovnakom čase ako kópia live gridu.",
        why: "Rozdiel oproti „Grid (kópia live)“ je spôsobený len dobehom.",
      },
    ],
    risk: "Keď kurz po skoku pokračuje ďalej rovnakým smerom, má v strate naraz viac pozícií (až 6 namiesto 1). Lab za 2 roky: o ~1,8k menej než kópia, v 2. roku o 0,6k viac — blackouty lab nevie modelovať, rozhodne súťaž.",
  },
};
