import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

const PUBLIC = ["/login", "/auth"];

/**
 * Obnoví Supabase session cookie a nepustí neprihláseného ďalej než na
 * /login. Skutočná ochrana dát je RLS v databáze — toto je len UX, aby
 * sa neukazovala prázdna aplikácia.
 */
export async function middleware(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll: (cookies) => {
          cookies.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          response = NextResponse.next({ request });
          cookies.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  const {
    data: { user },
  } = await supabase.auth.getUser();

  const path = request.nextUrl.pathname;
  const isPublic = PUBLIC.some((p) => path.startsWith(p));

  // E-mailový odkaz často pristane na koreni, nie na /auth/callback —
  // Supabase použije Site URL vždy, keď emailRedirectTo nie je medzi
  // povolenými Redirect URLs. Bez tejto vetvy by ho redirect na /login
  // zahodil aj s tokenom a používateľ by videl znova prihlasovací formulár.
  const hasAuthToken =
    request.nextUrl.searchParams.has("code") ||
    request.nextUrl.searchParams.has("token_hash");
  if (!user && hasAuthToken && !path.startsWith("/auth")) {
    const url = request.nextUrl.clone();
    url.pathname = "/auth/callback";
    return NextResponse.redirect(url);
  }

  if (!user && !isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }
  if (user && path === "/login") {
    const url = request.nextUrl.clone();
    url.pathname = "/";
    return NextResponse.redirect(url);
  }
  return response;
}

export const config = {
  // manifest a ikony musia byť dostupné aj neprihlásenému — prehliadač
  // ich ťahá pred akýmkoľvek prihlásením a bez nich sa aplikácia nedá
  // poriadne pridať na plochu.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|manifest.webmanifest|icon|apple-icon|.*\\.(?:svg|png|ico)$).*)",
  ],
};
