"use client";

import { useCallback, useEffect, useState } from "react";
import { createClient } from "@/lib/supabase";

/**
 * Načíta tabuľku a drží ju živú cez Realtime.
 *
 * Pri notifikácii načítavame celý dotaz znova namiesto skladania delty:
 * objemy sú malé (desiatky riadkov) a inkrementálne zliepanie by muselo
 * duplikovať filtre aj zoradenie — pri tejto veľkosti dát zbytočné riziko
 * rozídenia stavu.
 */
export function useLive<T>(
  table: string,
  build: (q: ReturnType<ReturnType<typeof createClient>["from"]>) => unknown,
  deps: unknown[] = [],
) {
  const [rows, setRows] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const supabase = createClient();
    const { data, error } = (await build(supabase.from(table))) as {
      data: T[] | null;
      error: { message: string } | null;
    };
    if (error) setError(error.message);
    else {
      setRows(data ?? []);
      setError(null);
    }
    setLoading(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    load();
    const supabase = createClient();
    const ch = supabase
      .channel(`live:${table}`)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table },
        () => load(),
      )
      .subscribe();
    return () => {
      supabase.removeChannel(ch);
    };
  }, [table, load]);

  return { rows, loading, error, reload: load };
}
