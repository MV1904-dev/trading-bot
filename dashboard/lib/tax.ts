/**
 * Odhad dane a zdravotného odvodu zo zisku z CFD — fyzická osoba, Slovensko.
 *
 * CFD sú ostatný príjem podľa § 8 ods. 1 písm. k) zákona o dani z príjmov.
 * Podľa stanoviska FR SR „Zdanenie príjmov z forexových obchodov":
 *  - zdaňuje sa len výsledok ZATVORENÝCH pozícií, otvorené sú fiktívny zisk/strata,
 *  - poplatky a podobné platby k derivátovým obchodom sú výdavok,
 *  - ak sú výdavky vyššie ako príjmy, na rozdiel sa neprihliada — strata
 *    sa nedá preniesť do ďalšieho roka ani započítať proti mzde.
 * Zdravotné poistenie sa z § 8 platí (v ročnom zúčtovaní), sociálne nie.
 * Oslobodenie 500 € sa neuplatňuje — zdroje sa nezhodujú, či naň CFD majú nárok.
 *
 * Je to ODHAD, nie daňové priznanie. Pásmo dane sa určuje z celého základu
 * dane za rok vrátane mzdy či živnosti — ten sem treba zadať ako OTHER_BASE,
 * inak výpočet predpokladá, že iné zdaniteľné príjmy nie sú.
 */

/** Základ dane z ostatných príjmov za rok (mzda, živnosť), v EUR. */
export const OTHER_BASE = 0;

type Rules = {
  health: number;
  /** [horná hranica základu dane, sadzba] — vzostupne */
  brackets: [number, number][];
};

/** Sadzby podľa roka. Nový rok treba doplniť po overení — inak sa použije
    posledný známy a dlaždica to označí. */
const RULES: Record<number, Rules> = {
  2026: {
    health: 0.16, // VšZP: z 15 % na 16 % od 1. 1. 2026
    brackets: [   // životné minimum k 1. 1. 2026 = 284,13 €
      [43_983.32, 0.19], // do 154,8 × ŽM
      [60_349.21, 0.25], // do 212,4 × ŽM
      [75_010.32, 0.3],  // do 264 × ŽM
      [Infinity, 0.35],
    ],
  },
};

function progressive(base: number, brackets: Rules["brackets"]): number {
  let tax = 0;
  let floor = 0;
  for (const [ceil, rate] of brackets) {
    if (base <= floor) break;
    tax += (Math.min(base, ceil) - floor) * rate;
    floor = ceil;
  }
  return tax;
}

export type CfdTax = {
  year: number;
  profit: number;      // realizovaný čistý výsledok za rok (po províziách a swape)
  base: number;        // zdaniteľný základ — strata sa nepočíta
  tax: number;
  health: number;
  net: number;         // čo z výsledku ostane
  taxRate: number;     // efektívna sadzba dane na tomto zisku
  healthRate: number;
  verified: boolean;   // false = sadzby pre tento rok nie sú v RULES
};

export function cfdTax(year: number, profit: number): CfdTax {
  const known = Object.keys(RULES).map(Number);
  const verified = year in RULES;
  const rules = RULES[verified ? year : Math.max(...known)];
  const base = Math.max(0, profit);
  // Zisk z CFD sa pripočíta k ostatnému základu — zdaní sa jeho prírastok.
  const tax = progressive(OTHER_BASE + base, rules.brackets)
            - progressive(OTHER_BASE, rules.brackets);
  const health = base * rules.health;
  return {
    year, profit, base, tax, health,
    net: profit - tax - health,
    taxRate: base > 0 ? tax / base : rules.brackets[0][1],
    healthRate: rules.health,
    verified,
  };
}
