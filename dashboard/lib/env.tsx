"use client";

import { createContext, useContext, useEffect, useState } from "react";

/** Rozmer zrkadlových dát. Bot beží naživo od 6. 8. 2026, demo história
 *  ostáva prístupná cez prepínač v hlavičke a „paper" je virtuálny účet
 *  súťažnej stratégie Strážca (paper_bot.py, od 18. 10. 2026). */
export type BotEnv = "live" | "paper" | "demo";

const EnvContext = createContext<{
  env: BotEnv;
  setEnv: (e: BotEnv) => void;
}>({ env: "live", setEnv: () => {} });

export function EnvProvider({ children }: { children: React.ReactNode }) {
  const [env, setEnvState] = useState<BotEnv>("live");

  useEffect(() => {
    const saved = localStorage.getItem("bot-env");
    if (saved === "demo" || saved === "live" || saved === "paper") setEnvState(saved);
  }, []);

  function setEnv(e: BotEnv) {
    setEnvState(e);
    try {
      localStorage.setItem("bot-env", e);
    } catch {}
  }

  return (
    <EnvContext.Provider value={{ env, setEnv }}>{children}</EnvContext.Provider>
  );
}

export function useEnv() {
  return useContext(EnvContext);
}
