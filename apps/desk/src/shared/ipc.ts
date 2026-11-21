/**
 * The single source of truth for renderer -> main IPC.
 *
 * Every channel the renderer may call is listed in `IpcChannels` with its request and
 * response types. The preload exposes only these channels (see `IPC_CHANNELS`), and the
 * main process registers a handler for each one (see `src/main/ipc.ts`). All channels are
 * read-only: the desk app never writes to the engine, Kalshi, or the user's files.
 *
 * To add a channel: add an entry here, add its name to `IPC_CHANNELS`, implement the
 * handler in `src/main/ipc.ts`, and add a method to `NemApi` + `src/preload/index.ts`.
 */

import type {
  KalshiApi,
  KalshiMarket,
  KalshiResult,
  KalshiSeries,
  OrderBook,
  SeriesListItem,
} from "./kalshi";

import type {
  CurveKey,
  CurvesResult,
  DataSource,
  SnapshotResult,
  SourceResult,
  SourcesState,
  TradesCsv,
  TradesFilter,
  TradesPage,
  TradesQuery,
} from "./data";

import type {
  DbApi,
  DbDepth,
  DbFeedQuery,
  DbFeedSeries,
  DbInfo,
  DbMarketsPage,
  DbMarketsQuery,
  DbResult,
  DbTopSeries,
} from "./db";

import type { VpsApi, VpsConfig, VpsSaveResult, VpsState, VpsSyncResult } from "./vps";

import type { UpdateState, UpdatesApi } from "./updates";

import type { SheetsApi, SheetsSaveRequest, SheetsSaveResult, SheetsState, SheetsSyncResult } from "./sheets";

import type { DiagCopyResult, Diagnostics, DiagnosticsApi } from "./diagnostics";

/** A place the desk app can read engine output from (DESK-003/004, see ./data.ts). */
export type { DataSource } from "./data";

export interface IpcChannels {
  "app:version": { request: void; response: string };
  "data:listSources": { request: void; response: DataSource[] };
  // DESK-003/004 data layer: the active source's files, read and validated in main.
  "data:snapshot": { request: void; response: SnapshotResult };
  "data:trades": { request: TradesQuery; response: TradesPage };
  "data:tradesCsv": { request: TradesFilter; response: TradesCsv };
  "data:curves": { request: { keys: CurveKey[] }; response: CurvesResult };
  "sources:list": { request: void; response: SourcesState };
  "sources:addFolder": { request: { path?: string }; response: SourceResult };
  "sources:addSample": { request: void; response: SourceResult };
  "sources:remove": { request: { id: string }; response: SourcesState };
  "sources:setActive": { request: { id: string }; response: SourcesState };
  // DESK-024 Explore: read-only public Kalshi data, fetched in main (see src/main/kalshi/).
  "kalshi:searchSeries": { request: string; response: KalshiResult<SeriesListItem[]> };
  "kalshi:series": { request: string; response: KalshiResult<KalshiSeries> };
  "kalshi:markets": { request: string; response: KalshiResult<KalshiMarket[]> };
  "kalshi:market": { request: string; response: KalshiResult<KalshiMarket> };
  "kalshi:orderbook": { request: string; response: KalshiResult<OrderBook> };
  "kalshi:copyStrategyCommand": { request: string; response: KalshiResult<string> };
  // DESK-010 SQLite reader: the active source's snapshot DB, read-only (src/main/db/).
  "db:info": { request: void; response: DbResult<DbInfo> };
  "db:markets": { request: DbMarketsQuery; response: DbResult<DbMarketsPage> };
  "db:top": { request: string; response: DbResult<DbTopSeries> };
  "db:depth": { request: { ticker: string; id?: number }; response: DbResult<DbDepth> };
  "db:feed": { request: DbFeedQuery; response: DbResult<DbFeedSeries> };
  "db:attach": { request: { id: string; path?: string }; response: SourceResult };
  "db:detach": { request: { id: string }; response: SourcesState };
  // DESK-013 VPS sync: ssh + rsync into a userData cache (src/main/vps/).
  "vps:get": { request: void; response: VpsState };
  "vps:save": { request: VpsConfig; response: VpsSaveResult };
  "vps:forget": { request: void; response: VpsState };
  "vps:sync": { request: void; response: VpsSyncResult };
  "vps:pickKey": { request: void; response: string | null };
  // DESK-019 update-available notice: GitHub Releases, GET only (src/main/updates/).
  "updates:get": { request: void; response: UpdateState };
  "updates:check": { request: void; response: UpdateState };
  "updates:setEnabled": { request: boolean; response: UpdateState };
  "updates:openRelease": { request: void; response: boolean };
  // DESK-012 Google Sheets source: public CSV export into a userData cache (src/main/sheets/).
  "sheets:get": { request: void; response: SheetsState };
  "sheets:save": { request: SheetsSaveRequest; response: SheetsSaveResult };
  "sheets:forget": { request: void; response: SheetsState };
  "sheets:sync": { request: void; response: SheetsSyncResult };
  // DESK-020 logs and diagnostics, redacted in main (src/main/diagnostics.ts).
  "diag:get": { request: void; response: Diagnostics };
  "diag:copy": { request: void; response: DiagCopyResult };
  "diag:revealLog": { request: void; response: boolean };
}

export type IpcChannel = keyof IpcChannels;
export type IpcRequest<C extends IpcChannel> = IpcChannels[C]["request"];
export type IpcResponse<C extends IpcChannel> = IpcChannels[C]["response"];

export const IPC_CHANNELS = [
  "app:version",
  "data:listSources",
  "data:snapshot",
  "data:trades",
  "data:tradesCsv",
  "data:curves",
  "sources:list",
  "sources:addFolder",
  "sources:addSample",
  "sources:remove",
  "sources:setActive",
  "kalshi:searchSeries",
  "kalshi:series",
  "kalshi:markets",
  "kalshi:market",
  "kalshi:orderbook",
  "kalshi:copyStrategyCommand",
  "db:info",
  "db:markets",
  "db:top",
  "db:depth",
  "db:feed",
  "db:attach",
  "db:detach",
  "vps:get",
  "vps:save",
  "vps:forget",
  "vps:sync",
  "vps:pickKey",
  "updates:get",
  "updates:check",
  "updates:setEnabled",
  "updates:openRelease",
  "sheets:get",
  "sheets:save",
  "sheets:forget",
  "sheets:sync",
  "diag:get",
  "diag:copy",
  "diag:revealLog",
] as const satisfies readonly IpcChannel[];

// Compile-time check that IPC_CHANNELS lists every channel in IpcChannels.
type Missing = Exclude<IpcChannel, (typeof IPC_CHANNELS)[number]>;
type AssertNever<T extends never> = T;
export type IpcChannelsComplete = AssertNever<Missing>;

export function isIpcChannel(value: unknown): value is IpcChannel {
  return typeof value === "string" && (IPC_CHANNELS as readonly string[]).includes(value);
}

/** The API the preload puts on `window.nem`. */
export interface NemApi {
  appVersion: () => Promise<string>;
  listSources: () => Promise<DataSource[]>;
  /** Engine output from the active source (read-only). */
  data: DataApi;
  /** The user's list of folder sources (stored in the app's own userData). */
  sources: SourcesApi;
  /** Read-only Kalshi public market data (Explore screen). */
  kalshi: KalshiApi;
  /** Platform string, used for Cmd vs Ctrl labels. */
  platform: string;
}

/** Main -> renderer push when the active source's files change (not an invoke channel). */
export const DATA_CHANGED_EVENT = "data:changed";

export interface DataApi {
  snapshot: () => Promise<SnapshotResult>;
  trades: (query: TradesQuery) => Promise<TradesPage>;
  tradesCsv: (filter: TradesFilter) => Promise<TradesCsv>;
  curves: (keys: CurveKey[]) => Promise<CurvesResult>;
  /** Calls back with the new revision; returns an unsubscribe function. */
  onChanged: (callback: (revision: number) => void) => () => void;
  /** DESK-010: read-only queries on the active source's snapshot database. */
  db: DbApi;
  /** DESK-019: "update available" check against GitHub Releases (never downloads). */
  updates: UpdatesApi;
  /** DESK-020: app, contract, sources, DB and recent log lines, redacted in main. */
  diagnostics: DiagnosticsApi;
}

export interface SourcesApi {
  list: () => Promise<SourcesState>;
  /** Without a path, main shows a folder picker. */
  addFolder: (path?: string) => Promise<SourceResult>;
  addSample: () => Promise<SourceResult>;
  remove: (id: string) => Promise<SourcesState>;
  setActive: (id: string) => Promise<SourcesState>;
  /** DESK-010: attach a snapshot database file to a source (no path: main shows a picker). */
  attachDb: (id: string, path?: string) => Promise<SourceResult>;
  detachDb: (id: string) => Promise<SourcesState>;
  /** DESK-013: VPS sync settings and "Sync now" (the cache becomes the "vps" source). */
  vps: VpsApi;
  /** DESK-012: a Google Sheet shared "anyone with the link" (the cache becomes the "sheets" source). */
  sheets: SheetsApi;
}
