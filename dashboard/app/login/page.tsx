"use client";

import { useState } from "react";
import { createClient, OWNER_EMAIL } from "@/lib/supabase";

export default function LoginPage() {
  const [email, setEmail] = useState(OWNER_EMAIL);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: {
        emailRedirectTo: `${window.location.origin}/auth/callback`,
      },
    });
    setBusy(false);
    if (error) setErr(error.message);
    else setSent(true);
  }

  return (
    <main className="flex min-h-dvh items-center justify-center">
      <div className="card w-full max-w-sm">
        <h1 className="text-lg font-semibold">Trading bot</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Prihlásenie odkazom na e-mail.
        </p>

        {sent ? (
          <p className="mt-6 rounded-lg bg-emerald-950/60 p-3 text-sm text-emerald-300">
            Odkaz je na ceste na <strong>{email}</strong>. Otvor ho na tomto
            zariadení.
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

        {err && <p className="mt-3 text-sm text-rose-400">{err}</p>}

        <p className="mt-6 text-xs text-zinc-600">
          Prístup k dátam je viazaný na jediný účet priamo v databáze — cudzie
          prihlásenie neuvidí nič ani po úspešnom prihlásení.
        </p>
      </div>
    </main>
  );
}
