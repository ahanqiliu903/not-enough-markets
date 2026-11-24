import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  curveId,
  type CurveKey,
  type DataError,
  type PortfolioData,
  type Snapshot,
  type StrategyRow,
} from "@shared/data";
import { nem, queryKeys } from "../lib/api";
import { useNav } from "../state/nav";

/** The active source's snapshot (summary + strategies + extras), read in main. */
export function useSnapshot() {
  return useQuery({ queryKey: queryKeys.snapshot, queryFn: () => nem().data.snapshot() });
}

export function useSourcesState() {
  return useQuery({ queryKey: queryKeys.sourcesState, queryFn: () => nem().sources.list() });
}

export function useCurves(keys: readonly CurveKey[]) {
  const ids = keys.map(curveId);
  return useQuery({
    queryKey: [...queryKeys.curves, ids],
    queryFn: () => nem().data.curves([...keys]),
    enabled: keys.length > 0,
  });
}

/** A portfolio's value over time (equity.csv, contract 1.1); `curve` null when absent. */
export function useEquity(portfolio: string) {
  return useQuery({ queryKey: [...queryKeys.equity, portfolio], queryFn: () => nem().data.equity(portfolio) });
}

/**
 * Mounted once by the shell: refetches every data query when main says the active
 * source's files changed, and reveals screens that need data (Backtests).
 */
export function useDataSync(): void {
  const client = useQueryClient();
  const setFlags = useNav((s) => s.setFlags);
  const snap = useSnapshot();
  useEffect(
    () =>
      nem().data.onChanged(() => {
        void client.invalidateQueries({ queryKey: ["data"] });
        void client.invalidateQueries({ queryKey: queryKeys.sourcesState });
      }),
    [client],
  );
  const hasBacktests = snap.data?.state === "ok" && snap.data.snapshot.backtests.length > 0;
  useEffect(() => setFlags({ hasBacktests }), [hasBacktests, setFlags]);
}

/** Re-renders every `ms` so age-based UI (stale banner) stays current. */
export function useNow(ms = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

export interface StrategyRef {
  portfolio: PortfolioData;
  row: StrategyRow;
  id: string;
}

/** Every strategy row in the snapshot (retired and paused included), portfolio order. */
export function allStrategies(s: Snapshot): StrategyRef[] {
  return s.portfolios.flatMap((p) =>
    (p.strategies ?? []).map((row) => ({ portfolio: p, row, id: `${p.name}/${row.strategy}` })),
  );
}

export function findStrategy(s: Snapshot, portfolio: string | undefined, strategy: string | undefined): StrategyRef | null {
  if (portfolio === undefined || strategy === undefined) return null;
  return allStrategies(s).find((r) => r.portfolio.name === portfolio && r.row.strategy === strategy) ?? null;
}

export function errorsForFile(errors: readonly DataError[], file: string): DataError[] {
  return errors.filter((e) => e.file === file || e.file.endsWith(`/${file}`));
}
