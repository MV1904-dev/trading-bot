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
  );
}

export const OWNER_EMAIL = process.env.NEXT_PUBLIC_OWNER_EMAIL ?? "";
