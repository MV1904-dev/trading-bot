"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { createClient } from "@/lib/supabase";

/** Rozmer zrkadlových dát: „live" (bot obchoduje naživo od 6. 8. 2026),
 *  „demo" (archív checkpointu) a virtuálne účty súťažných stratégií
 *  (paper_bot.py) — ich env začína „paper" a ich zoznam sa číta z bot_state,
 *  takže nová stratégia sa v dashboarde objaví sama. */
export type BotEnv = string;

export type Virtual = { env: string; name: string; started_at: string | null };

export const isVirtual = (env: string) => env.startsWith("paper");

const EnvContext = createContext<{
  env: BotEnv;
  setEnv: (e: BotEnv) => void;
  virtuals: Virtual[];
}>({ env: "live", setEnv: () => {}, virtuals: [] });

export function EnvProvider({ children }: { children: React.ReactNode }) {
  const [env, setEnvState] = useState<BotEnv>("live");
  const [virtuals, setVirtuals] = useState<Virtual[]>([]);

  useEffect(() => {
    const saved = localStorage.getItem("bot-env");
    if (saved === "demo" || saved === "live" || (saved && isVirtual(saved))) setEnvState(saved);
  }, []);

  // Znova pri zmene účtu: pri prvom načítaní môže byť používateľ ešte
  // neprihlásený (RLS vráti prázdno) a nový virtuálny účet pribudne za behu.
  useEffect(() => {
    let alive = true;
    createClient().from("bot_state").select("env,config").like("env", "paper%")
      .then(({ data }) => {
        if (!alive || !data) return;
        const list = data.map((r) => {
          const c = (r.config ?? {}) as Record<string, unknown>;
          return {
            env: r.env as string,
            name: (c.strategy_name as string) || (r.env as string),
            started_at: (c.started_at as string) ?? null,
          };
        });
        list.sort((a, b) => (a.started_at ?? "").localeCompare(b.started_at ?? ""));
        setVirtuals(list);
      });
    return () => { alive = false; };
  }, [env]);

  function setEnv(e: BotEnv) {
    setEnvState(e);
    try {
      localStorage.setItem("bot-env", e);
      if (isVirtual(e)) localStorage.setItem("bot-env-sim", e);
    } catch {}
  }

  return (
    <EnvContext.Provider value={{ env, setEnv, virtuals }}>{children}</EnvContext.Provider>
  );
}

export function useEnv() {
  return useContext(EnvContext);
}
