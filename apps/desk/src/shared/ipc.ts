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
}

export interface SourcesApi {
  list: () => Promise<SourcesState>;
  /** Without a path, main shows a folder picker. */
  addFolder: (path?: string) => Promise<SourceResult>;
  addSample: () => Promise<SourceResult>;
  remove: (id: string) => Promise<SourcesState>;
  setActive: (id: string) => Promise<SourcesState>;
}
