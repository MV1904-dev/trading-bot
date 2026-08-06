"use client";

import { createContext, useContext, useEffect, useState } from "react";

/** Demo/live rozmer zrkadlových dát. Bot beží naživo od 6. 8. 2026,
 *  demo história ostáva prístupná cez prepínač v hlavičke. */
export type BotEnv = "live" | "demo";

const EnvContext = createContext<{
  env: BotEnv;
  setEnv: (e: BotEnv) => void;
}>({ env: "live", setEnv: () => {} });

export function EnvProvider({ children }: { children: React.ReactNode }) {
  const [env, setEnvState] = useState<BotEnv>("live");

  useEffect(() => {
    const saved = localStorage.getItem("bot-env");
    if (saved === "demo" || saved === "live") setEnvState(saved);
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
