/**
 * SQLite reader types (DESK-010) shared by main and renderer. The database is the engine's
 * `data/nem.db` schema (`src/nem/store/schema.sql`, `PRAGMA user_version`), read only from
 * a consistent snapshot file (`data/snapshot/nem.db`) or the contract fixture. Values are
 * recorded prices and feed values as stored; nothing here is recomputed.
 */

/**
 * `PRAGMA user_version` values this build reads. v4 only adds the engine's `manual_lots` /
 * `manual_fills` tables (imported manual trading), v5 only `settlement_checks`
 * (`nem settle verify`), v6 only `forecast_backfill` (`nem backfill forecasts`), v7
 * only `exit_learning` (exited trades learned from at settlement) and v8 only the `trades`
 * columns `excluded_at` / `exclude_reason` (`nem trade exclude`), v9 only the order lifecycle
 * columns of `orders` / `fills` (atomic execution); nothing read here changed.
 */
export const DB_SCHEMA_VERSIONS = [3, 4, 5, 6, 7, 8, 9] as const;

export type DbErrorKind =
  /** The active source has no database attached or found. */
  | "no_db"
  | "missing_file"
  /** A `-wal`/`-journal` file sits next to it: a live database, not a snapshot. */
  | "live_db"
  | "locked"
  | "not_sqlite"
  | "schema_version"
  | "query"
  | "bad_request";

export interface DbError {
  kind: DbErrorKind;
  message: string;
  file?: string;
  expected?: string;
  found?: string;
}

export type DbResult<T> = { ok: true; value: T } | { ok: false; error: DbError };

export interface DbSeriesInfo {
  series: string;
  snapshots: number;
  markets: number;
  firstTs: string;
  lastTs: string;
}

export interface DbFeedInfo {
  feed: string;
  key: string;
  values: number;
  firstKnownAt: string;
  lastKnownAt: string;
}

export interface DbInfo {
  path: string;
  schemaVersion: number;
  sizeBytes: number;
  series: DbSeriesInfo[];
  feeds: DbFeedInfo[];
  /** Newest snapshot `ts` (ISO, UTC), for the stale banner. */
  newestTs: string | null;
}

export interface DbMarket {
  ticker: string;
  series: string;
  windowId: string;
  openTime: string;
  closeTime: string;
  snapshots: number;
  firstTs: string;
  lastTs: string;
  strikeType: string | null;
  floorStrike: number | null;
  capStrike: number | null;
  /** Settlement as recorded, null while unsettled. */
  result: "yes" | "no" | null;
  settledAt: string | null;
}

export interface DbMarketsQuery {
  series: string;
  /** Case-insensitive substring of the ticker. */
  text?: string;
  offset: number;
  limit: number;
}

export interface DbMarketsPage {
  total: number;
  offset: number;
  rows: DbMarket[];
}

/** One recorded poll: top of book in dollars (null = no quote). */
export interface DbTopPoint {
  /** Snapshot row id, used to ask for its depth. */
  id: number;
  /** Unix seconds. */
  t: number;
  yesBid: number | null;
  yesAsk: number | null;
  noBid: number | null;
  noAsk: number | null;
}

export interface DbTopSeries {
  ticker: string;
  points: DbTopPoint[];
  /** True when the market had more snapshots than the limit (the newest are kept). */
  truncated: boolean;
}

/** `[price_dollars, size]`, best first, as recorded in `depth_json`. */
export type DbLevel = [number, number];

export interface DbDepth {
  id: number;
  ticker: string;
  ts: string;
  yes: DbLevel[];
  no: DbLevel[];
}

export interface DbFeedQuery {
  feed: string;
  key: string;
  /** Unix seconds, inclusive. */
  from: number;
  to: number;
  limit?: number;
}

export interface DbFeedPoint {
  /** `known_at` as Unix seconds: when the value became knowable. */
  knownAt: number;
  value: number;
}

export interface DbFeedSeries {
  feed: string;
  key: string;
  points: DbFeedPoint[];
  truncated: boolean;
}

export interface DbApi {
  info: () => Promise<DbResult<DbInfo>>;
  markets: (query: DbMarketsQuery) => Promise<DbResult<DbMarketsPage>>;
  top: (ticker: string) => Promise<DbResult<DbTopSeries>>;
  depth: (ticker: string, id?: number) => Promise<DbResult<DbDepth>>;
  feed: (query: DbFeedQuery) => Promise<DbResult<DbFeedSeries>>;
}

/**
 * The engine writes `isoformat` with microseconds and `+00:00`; turn it into Unix seconds
 * without relying on the JS date parser accepting six fractional digits.
 */
export function dbTimeToUnix(ts: string): number {
  const m = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$/.exec(ts);
  if (m?.[1] === undefined) return Number.NaN;
  const frac = m[2] !== undefined ? Number(`0.${m[2]}`) : 0;
  const zone = m[3] === undefined || m[3] === "Z" ? "+00:00" : m[3];
  return Date.parse(`${m[1]}${zone}`) / 1000 + frac;
}
