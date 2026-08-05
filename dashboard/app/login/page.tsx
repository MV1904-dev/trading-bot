"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient, OWNER_EMAIL } from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState(OWNER_EMAIL);
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function signIn(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      setErr(
        error.code === "invalid_credentials"
          ? "Nesprávny e-mail alebo heslo."
          : `${error.message} (${error.code ?? error.status})`,
      );
      setBusy(false);
      return;
    }
    router.push("/");
    router.refresh();
  }

  return (
    <main className="flex min-h-dvh items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-xl border border-line bg-solid p-5">
        <h1 className="text-lg font-semibold">Trading bot</h1>
        <p className="mt-1 text-sm text-muted">
          Prihlásenie heslom — rovnaké ako v Portfóliu.
        </p>

        <form onSubmit={signIn} className="mt-6 space-y-3">
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="input"
            placeholder="e-mail"
          />
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="input"
            placeholder="heslo"
          />
          <button type="submit" disabled={busy} className="btn-primary w-full">
            {busy ? "Prihlasujem…" : "Prihlásiť sa"}
          </button>
        </form>

        {err && <p className="mt-3 text-sm text-neg">{err}</p>}

        <p className="mt-6 text-xs text-faint">
          Prístup k dátam je viazaný na jediný účet priamo v databáze — cudzie
          prihlásenie neuvidí nič ani po úspešnom prihlásení.
        </p>
      </div>
    </main>
  );
}
