import { createBrowserClient } from "@supabase/ssr";

/**
 * Klient pre prehliadač. Používa publishable (anon) kľúč — ten je verejný
 * zámerne; prístup k dátam drží RLS cez is_owner() na serveri.
 *
 * Service key tu nemá čo hľadať: dashboard nesmie vedieť nič, čo by mu dalo
 * moc nad brokerom. Jediná zapisovacia cesta je INSERT do `commands`.
 */
export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      // Implicit namiesto PKCE: e-mailový odkaz sa overí na serveri Supabase
      // a tokeny prídu vo fragmente. PKCE viaže odkaz na prehliadač, ktorý si
      // ho vypýtal (verifier v cookie) — odkaz otvorený cez Gmail tak končil
      // na "PKCE code verifier not found", a každé nové odoslanie zneplatnilo
      // verifier predchádzajúceho odkazu.
      auth: { flowType: "implicit" },
    },
  );
}

export const OWNER_EMAIL = process.env.NEXT_PUBLIC_OWNER_EMAIL ?? "";
