"use client";

import { useEffect, useState } from "react";
import { createClient, OWNER_EMAIL } from "@/lib/supabase";

export default function LoginPage() {
  const [email, setEmail] = useState(OWNER_EMAIL);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Odkaz môže session doručiť aj vo fragmente (#access_token), ktorý server
  // nikdy nevidí — a zlyhanie z callbacku príde v ?error=. Bez tohto oboje
  // skončilo tichým návratom na formulár.
  useEffect(() => {
    const reason = new URLSearchParams(window.location.search).get("error");
    if (reason) {
      setErr(
        reason === "chyba_odkazu"
          ? "Odkaz neobsahoval prihlasovací token."
          : `Odkaz sa nepodarilo použiť: ${reason}`,
      );
    }

    const hash = new URLSearchParams(window.location.hash.slice(1));
    const access_token = hash.get("access_token");
    const refresh_token = hash.get("refresh_token");
    if (hash.get("error_description")) {
      setErr(hash.get("error_description"));
      return;
    }
    if (!access_token || !refresh_token) return;

    setBusy(true);
    createClient()
      .auth.setSession({ access_token, refresh_token })
      .then(({ error }) => {
        if (error) {
          setErr(error.message);
          setBusy(false);
          return;
        }
        window.location.replace("/");
      });
  }, []);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: {
        // Tokeny prídu vo fragmente (#access_token) — ten vidí len prehliadač,
        // takže cieľ je /login, kde ich spracuje fragment handler nižšie.
        emailRedirectTo: `${window.location.origin}/login`,
      },
    });
    setBusy(false);
    if (error) setErr(error.message);
    else setSent(true);
  }

  return (
    <main className="flex min-h-dvh items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-xl border border-line bg-solid p-5">
        <h1 className="text-lg font-semibold">Trading bot</h1>
        <p className="mt-1 text-sm text-muted">
          Prihlásenie odkazom na e-mail.
        </p>

        {sent ? (
          <p className="mt-6 rounded-lg bg-emerald-600/10 p-3 text-sm text-pos">
            Odkaz je na ceste na <strong>{email}</strong>. Otvor{" "}
            <strong>najnovší</strong> e-mail — staršie odkazy už neplatia.
          </p>
        ) : (
          <form onSubmit={send} className="mt-6 space-y-3">
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="input"
              placeholder="e-mail"
            />
            <button type="submit" disabled={busy} className="btn-primary w-full">
              {busy ? "Odosielam…" : "Poslať odkaz"}
            </button>
          </form>
        )}

        {err && <p className="mt-3 text-sm text-neg">{err}</p>}

        <p className="mt-6 text-xs text-faint">
          Prístup k dátam je viazaný na jediný účet priamo v databáze — cudzie
          prihlásenie neuvidí nič ani po úspešnom prihlásení.
        </p>
      </div>
    </main>
  );
}
