import type { NemApi } from "@shared/ipc";
import type { KalshiResult } from "@shared/kalshi";
import type { DbResult } from "@shared/db";
import type { VpsState } from "@shared/vps";
import type { UpdateState } from "@shared/updates";
import type { SheetsState } from "@shared/sheets";
import type { HaltState } from "@shared/halt";

const kalshiOffline = <T>(): Promise<KalshiResult<T>> =>
  Promise.resolve({ ok: false, error: { kind: "offline", message: "Not running inside NEM Desk" } });

const offlineSources = { sources: [], activeId: null, sampleAvailable: false };

const dbOffline = <T>(): Promise<DbResult<T>> =>
  Promise.resolve({ ok: false, error: { kind: "no_db", message: "Not running inside NEM Desk" } });

const offlineUpdates: UpdateState = { enabled: false, current: "dev", checking: false, last: null };

const offlineSheets: SheetsState = {
  configured: false,
  sheetMasked: null,
  portfolios: [],
  intervalMinutes: 5,
  syncing: false,
  lastSync: null,
  nextPollAt: null,
};
const sheetsOffline = { kind: "not_configured" as const, message: "Not running inside NEM Desk" };

const offlineVps: VpsState = { config: null, cacheDir: "", syncing: false, lastSync: null };

const offlineHalt: HaltState = { enabled: false, target: null, runsAs: null, portfolios: [], strategies: [], halts: [], running: false };

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
    equity: () => Promise.resolve({ ok: true, curve: null }),
    onChanged: () => () => undefined,
    db: { info: dbOffline, markets: dbOffline, top: dbOffline, depth: dbOffline, feed: dbOffline },
    updates: {
      get: () => Promise.resolve(offlineUpdates),
      check: () => Promise.resolve(offlineUpdates),
      setEnabled: () => Promise.resolve(offlineUpdates),
      openRelease: () => Promise.resolve(false),
    },
    diagnostics: {
      get: () => Promise.reject(new Error("Not running inside NEM Desk")),
      copy: () => Promise.resolve({ ok: false, message: "Not running inside NEM Desk" }),
      revealLog: () => Promise.resolve(false),
    },
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
      halt: {
        get: () => Promise.resolve(offlineHalt),
        setEnabled: () => Promise.resolve(offlineHalt),
        run: () =>
          Promise.resolve({ ok: false, command: null, output: "", error: { kind: "disabled", message: "Not running inside NEM Desk" }, state: offlineHalt }),
      },
    },
    sheets: {
      get: () => Promise.resolve(offlineSheets),
      save: () => Promise.resolve({ ok: false, state: offlineSheets, error: sheetsOffline }),
      forget: () => Promise.resolve(offlineSheets),
      sync: () =>
        Promise.resolve({ state: offlineSheets, sync: { at: new Date(0).toISOString(), ok: false, tabs: [], error: sheetsOffline } }),
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
  equity: ["data", "equity"] as const,
  kalshi: ["kalshi"] as const,
  // Under "data" so a data:changed push (new snapshot DB, VPS sync) refetches them too.
  db: ["data", "db"] as const,
  vps: ["vps"] as const,
  // Not under "data": a data:changed push needn't refetch it (it never hits the network anyway).
  updates: ["updates"] as const,
  sheets: ["sheets"] as const,
  diagnostics: ["diagnostics"] as const,
  halt: ["halt"] as const,
};
