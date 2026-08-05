import { createServerClient } from "@supabase/ssr";
import type { EmailOtpType } from "@supabase/supabase-js";
import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Dokončí prihlásenie z e-mailového odkazu.
 *
 * Supabase pošle jednu z dvoch podôb a treba zvládnuť obe:
 *  - `?code=` (PKCE) — vyžaduje verifier v cookie, čiže odkaz musí otvoriť
 *    ten istý prehliadač, ktorý si ho vypýtal,
 *  - `?token_hash=&type=` — overí sa na serveri a funguje aj vtedy, keď
 *    odkaz otvorí iný prehliadač (typicky in-app prehliadač Gmailu).
 * Recovery odkazy z Supabase dashboardu chodia v druhej podobe.
 */
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const code = params.get("code");
  const tokenHash = params.get("token_hash");
  const type = params.get("type") as EmailOtpType | null;
  const origin = request.nextUrl.origin;

  if (!code && !tokenHash) {
    return NextResponse.redirect(`${origin}/login?error=chyba_odkazu`);
  }

  const store = await cookies();
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll: () => store.getAll(),
        setAll: (list) =>
          list.forEach(({ name, value, options }) =>
            store.set(name, value, options),
          ),
      },
    },
  );

  const { error } = code
    ? await supabase.auth.exchangeCodeForSession(code)
    : await supabase.auth.verifyOtp({
        type: type ?? "magiclink",
        token_hash: tokenHash!,
      });

  if (error) {
    // Dôvod ide do URL, nech sa nezobrazí len prázdny formulár.
    return NextResponse.redirect(
      `${origin}/login?error=${encodeURIComponent(error.message)}`,
    );
  }
  return NextResponse.redirect(origin);
}
