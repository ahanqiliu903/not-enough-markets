import type { NemApi } from "@shared/ipc";
import type { KalshiResult } from "@shared/kalshi";
import type { DbResult } from "@shared/db";
import type { VpsState } from "@shared/vps";

const kalshiOffline = <T>(): Promise<KalshiResult<T>> =>
  Promise.resolve({ ok: false, error: { kind: "offline", message: "Not running inside NEM Desk" } });

const offlineSources = { sources: [], activeId: null, sampleAvailable: false };

const dbOffline = <T>(): Promise<DbResult<T>> =>
  Promise.resolve({ ok: false, error: { kind: "no_db", message: "Not running inside NEM Desk" } });

const offlineVps: VpsState = { config: null, cacheDir: "", syncing: false, lastSync: null };

/**
 * The renderer's only door to the main process. Outside Electron (unit tests, a plain
 * browser) `window.nem` is missing and this returns an offline stub, so screens render
 * their empty states instead of crashing.
 */
const offline: NemApi = {
  appVersion: () => Promise.resolve("dev"),
  listSources: () => Promise.resolve([]),
  data: {
    snapshot: () => Promise.resolve({ state: "none" }),
    trades: () => Promise.resolve({ ok: true, revision: 0, total: 0, offset: 0, rows: [] }),
    tradesCsv: () => Promise.resolve({ ok: true, csv: "", rows: 0 }),
    curves: () => Promise.resolve({ ok: true, curves: [] }),
    onChanged: () => () => undefined,
    db: { info: dbOffline, markets: dbOffline, top: dbOffline, depth: dbOffline, feed: dbOffline },
  },
  sources: {
    list: () => Promise.resolve({ sources: [], activeId: null, sampleAvailable: false }),
    addFolder: () => Promise.resolve({ ok: false, state: offlineSources, message: "Not running inside NEM Desk" }),
    addSample: () => Promise.resolve({ ok: false, state: offlineSources, message: "Not running inside NEM Desk" }),
    remove: () => Promise.resolve(offlineSources),
    setActive: () => Promise.resolve(offlineSources),
    attachDb: () => Promise.resolve({ ok: false, state: offlineSources, message: "Not running inside NEM Desk" }),
    detachDb: () => Promise.resolve(offlineSources),
    vps: {
      get: () => Promise.resolve(offlineVps),
      save: () =>
        Promise.resolve({ ok: false, state: offlineVps, error: { kind: "not_configured", message: "Not running inside NEM Desk" } }),
      forget: () => Promise.resolve(offlineVps),
      sync: () =>
        Promise.resolve({
          state: offlineVps,
          sync: { at: new Date(0).toISOString(), ok: false, steps: [], error: { kind: "not_configured", message: "Not running inside NEM Desk" } },
        }),
      pickKey: () => Promise.resolve(null),
    },
  },
  kalshi: {
    searchSeries: kalshiOffline,
    series: kalshiOffline,
    markets: kalshiOffline,
    market: kalshiOffline,
    orderbook: kalshiOffline,
    copyStrategyCommand: kalshiOffline,
  },
  platform: typeof navigator !== "undefined" ? navigator.platform : "",
};

export function nem(): NemApi {
  return (typeof window !== "undefined" ? window.nem : undefined) ?? offline;
}

/** TanStack Query keys, centralized so `r` (refresh) can invalidate everything. */
export const queryKeys = {
  appVersion: ["app", "version"] as const,
  sources: ["data", "sources"] as const,
  sourcesState: ["sources", "state"] as const,
  snapshot: ["data", "snapshot"] as const,
  trades: ["data", "trades"] as const,
  curves: ["data", "curves"] as const,
  kalshi: ["kalshi"] as const,
  // Under "data" so a data:changed push (new snapshot DB, VPS sync) refetches them too.
  db: ["data", "db"] as const,
  vps: ["vps"] as const,
};
