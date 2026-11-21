import { existsSync, statSync } from "node:fs";
import { basename } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { pathToFileURL } from "node:url";
import {
  DB_SCHEMA_VERSIONS,
  dbTimeToUnix,
  type DbDepth,
  type DbError,
  type DbFeedInfo,
  type DbFeedQuery,
  type DbFeedSeries,
  type DbInfo,
  type DbLevel,
  type DbMarket,
  type DbMarketsPage,
  type DbMarketsQuery,
  type DbResult,
  type DbSeriesInfo,
  type DbTopSeries,
} from "@shared/db";

/**
 * Read-only SQLite reader (DESK-010) on Node's built-in `node:sqlite` (no native module to
 * rebuild for Electron). The file is opened with `readOnly` and the `immutable=1` URI flag:
 * SQLite then takes no locks and creates no `-wal`/`-shm` files, so even a WAL-mode
 * snapshot in a read-only folder opens, and nothing is ever written. That is only safe
 * because sources are snapshot files that are replaced atomically (rsync and
 * `deploy/snapshot.sh` rename), never edited in place; a changed file (inode, size or
 * mtime) is reopened, and a file with a live `-wal`/`-journal` beside it is refused.
 * Every query is limited; every failure comes back as a `DbError` value.
 */

export const MAX_TOP_POINTS = 5000;
export const MAX_FEED_POINTS = 20000;
export const MAX_MARKETS_PAGE = 500;

type Row = Record<string, unknown>;

function err(kind: DbError["kind"], message: string, extra: Partial<DbError> = {}): { ok: false; error: DbError } {
  return { ok: false, error: { kind, message, ...extra } };
}

function sqliteError(e: unknown, file: string): DbError {
  const msg = e instanceof Error ? e.message : String(e);
  const code = (e as { errcode?: unknown }).errcode;
  const primary = typeof code === "number" ? code & 0xff : -1;
  // SQLITE_BUSY 5, SQLITE_LOCKED 6, SQLITE_NOTADB 26, SQLITE_CANTOPEN 14.
  if (primary === 5 || primary === 6) return { kind: "locked", file, message: `Database is locked: ${msg}` };
  if (primary === 26) return { kind: "not_sqlite", file, message: `Not an SQLite database: ${msg}` };
  if (primary === 14) return { kind: "missing_file", file, message: `Cannot open the database: ${msg}` };
  return { kind: "query", file, message: msg };
}

/** Unix seconds -> the engine's text format, so range filters compare as text (and use indexes). */
export function unixToDbTime(sec: number, end = false): string {
  const iso = new Date(Math.floor(sec * 1000)).toISOString();
  return `${iso.slice(0, 23)}${end ? "999" : "000"}+00:00`;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const text = (v: unknown): string =>
  typeof v === "string" ? v : typeof v === "number" || typeof v === "bigint" ? String(v) : "";
const textOrNull = (v: unknown): string | null => (typeof v === "string" ? v : null);

function parseLevels(v: unknown): DbLevel[] {
  if (!Array.isArray(v)) return [];
  const out: DbLevel[] = [];
  for (const l of v as unknown[]) {
    if (Array.isArray(l) && typeof l[0] === "number" && typeof l[1] === "number") out.push([l[0], l[1]]);
  }
  return out;
}

export function parseDepthJson(s: string): { yes: DbLevel[]; no: DbLevel[] } {
  try {
    const raw = JSON.parse(s) as Record<string, unknown>;
    return { yes: parseLevels(raw["yes"]), no: parseLevels(raw["no"]) };
  } catch {
    return { yes: [], no: [] };
  }
}

interface Open {
  db: DatabaseSync;
  fp: string;
  version: number;
  info: DbInfo | null;
}

function fileFp(path: string): string | null {
  try {
    const s = statSync(path);
    return s.isFile() ? `${s.ino}:${s.size}:${s.mtimeMs}` : null;
  } catch {
    return null;
  }
}

/** Why a path cannot be a snapshot source, or null when it looks fine. Exported for attach. */
export function checkDbFile(path: string): DbError | null {
  const file = basename(path);
  if (fileFp(path) === null) return { kind: "missing_file", file: path, message: `Database file not found: ${path}` };
  if (existsSync(`${path}-wal`) || existsSync(`${path}-journal`)) {
    return {
      kind: "live_db",
      file: path,
      message: `${file} has a -wal/-journal file beside it, so it is a live database. Point NEM Desk at a snapshot instead (data/snapshot/nem.db from deploy/snapshot.sh).`,
    };
  }
  return null;
}

export class DbReader {
  private open: (Open & { path: string }) | null = null;

  private connect(path: string): DbResult<Open> {
    const bad = checkDbFile(path);
    if (bad !== null) {
      this.close();
      return { ok: false, error: bad };
    }
    const fp = fileFp(path) ?? "";
    if (this.open?.path === path && this.open.fp === fp) return { ok: true, value: this.open };
    this.close();
    let db: DatabaseSync | null = null;
    try {
      const url = pathToFileURL(path);
      url.searchParams.set("immutable", "1");
      db = new DatabaseSync(url, { readOnly: true, timeout: 2000 });
      const v = (db.prepare("PRAGMA user_version").get() as Row | undefined)?.["user_version"];
      const version = typeof v === "number" ? v : -1;
      if (!(DB_SCHEMA_VERSIONS as readonly number[]).includes(version)) {
        db.close();
        return err("schema_version", `Database schema v${version} is not one this build reads.`, {
          file: path,
          expected: DB_SCHEMA_VERSIONS.join(", "),
          found: String(version),
        });
      }
      this.open = { db, fp, version, info: null, path };
      return { ok: true, value: this.open };
    } catch (e) {
      try {
        db?.close();
      } catch {
        // already closed
      }
      return { ok: false, error: sqliteError(e, path) };
    }
  }

  private run<T>(path: string, fn: (o: Open) => T): DbResult<T> {
    const c = this.connect(path);
    if (!c.ok) return c;
    try {
      return { ok: true, value: fn(c.value) };
    } catch (e) {
      return { ok: false, error: sqliteError(e, path) };
    }
  }

  info(path: string): DbResult<DbInfo> {
    return this.run(path, (o) => {
      if (o.info !== null) return o.info;
      const series = (
        o.db
          .prepare(
            `SELECT series, COUNT(*) AS n, COUNT(DISTINCT ticker) AS markets, MIN(ts) AS first, MAX(ts) AS last
             FROM snapshots GROUP BY series ORDER BY series LIMIT 500`,
          )
          .all() as Row[]
      ).map(
        (r): DbSeriesInfo => ({
          series: text(r["series"]),
          snapshots: num(r["n"]) ?? 0,
          markets: num(r["markets"]) ?? 0,
          firstTs: text(r["first"]),
          lastTs: text(r["last"]),
        }),
      );
      const feeds = (
        o.db
          .prepare(
            `SELECT feed, key, COUNT(*) AS n, MIN(known_at) AS first, MAX(known_at) AS last
             FROM feed_values GROUP BY feed, key ORDER BY feed, key LIMIT 500`,
          )
          .all() as Row[]
      ).map(
        (r): DbFeedInfo => ({
          feed: text(r["feed"]),
          key: text(r["key"]),
          values: num(r["n"]) ?? 0,
          firstKnownAt: text(r["first"]),
          lastKnownAt: text(r["last"]),
        }),
      );
      const newest = series.reduce<string | null>((m, s) => (m === null || s.lastTs > m ? s.lastTs : m), null);
      let sizeBytes = 0;
      try {
        sizeBytes = statSync(path).size;
      } catch {
        // stat raced a replace; the size is cosmetic
      }
      o.info = { path, schemaVersion: o.version, sizeBytes, series, feeds, newestTs: newest };
      return o.info;
    });
  }

  markets(path: string, q: DbMarketsQuery): DbResult<DbMarketsPage> {
    return this.run(path, (o) => {
      const like = q.text !== undefined ? `%${q.text.replace(/[\\%_]/g, (c) => `\\${c}`)}%` : "%";
      const where = "s.series = ? AND s.ticker LIKE ? ESCAPE '\\'";
      const total = num((o.db.prepare(`SELECT COUNT(DISTINCT s.ticker) AS n FROM snapshots s WHERE ${where}`).get(q.series, like) as Row | undefined)?.["n"]) ?? 0;
      const rows = (
        o.db
          .prepare(
            `SELECT s.ticker, s.series, MIN(s.window_id) AS window_id, MIN(s.open_time) AS open_time,
                    MAX(s.close_time) AS close_time, COUNT(*) AS n, MIN(s.ts) AS first, MAX(s.ts) AS last,
                    MAX(s.strike_type) AS strike_type, MAX(s.floor_strike) AS floor_strike,
                    MAX(s.cap_strike) AS cap_strike, st.result, st.settled_at
             FROM snapshots s LEFT JOIN settlements st ON st.ticker = s.ticker
             WHERE ${where}
             GROUP BY s.ticker ORDER BY close_time DESC, s.ticker LIMIT ? OFFSET ?`,
          )
          .all(q.series, like, q.limit, q.offset) as Row[]
      ).map(
        (r): DbMarket => ({
          ticker: text(r["ticker"]),
          series: text(r["series"]),
          windowId: text(r["window_id"]),
          openTime: text(r["open_time"]),
          closeTime: text(r["close_time"]),
          snapshots: num(r["n"]) ?? 0,
          firstTs: text(r["first"]),
          lastTs: text(r["last"]),
          strikeType: textOrNull(r["strike_type"]),
          floorStrike: num(r["floor_strike"]),
          capStrike: num(r["cap_strike"]),
          result: r["result"] === "yes" || r["result"] === "no" ? r["result"] : null,
          settledAt: textOrNull(r["settled_at"]),
        }),
      );
      return { total, offset: q.offset, rows };
    });
  }

  top(path: string, ticker: string): DbResult<DbTopSeries> {
    return this.run(path, (o) => {
      const rows = o.db
        .prepare(
          `SELECT id, ts, yes_bid, yes_ask, no_bid, no_ask FROM snapshots
           WHERE ticker = ? ORDER BY ts DESC, id DESC LIMIT ?`,
        )
        .all(ticker, MAX_TOP_POINTS + 1) as Row[];
      const truncated = rows.length > MAX_TOP_POINTS;
      const points = rows
        .slice(0, MAX_TOP_POINTS)
        .reverse()
        .map((r) => ({
          id: num(r["id"]) ?? 0,
          t: dbTimeToUnix(text(r["ts"])),
          yesBid: num(r["yes_bid"]),
          yesAsk: num(r["yes_ask"]),
          noBid: num(r["no_bid"]),
          noAsk: num(r["no_ask"]),
        }));
      return { ticker, points, truncated };
    });
  }

  /** Depth of one snapshot (by id), or the market's newest when `id` is omitted. */
  depth(path: string, ticker: string, id?: number): DbResult<DbDepth> {
    const r = this.run(path, (o) => {
      const row = (
        id !== undefined
          ? o.db.prepare("SELECT id, ticker, ts, depth_json FROM snapshots WHERE ticker = ? AND id = ?").get(ticker, id)
          : o.db
              .prepare("SELECT id, ticker, ts, depth_json FROM snapshots WHERE ticker = ? ORDER BY ts DESC, id DESC LIMIT 1")
              .get(ticker)
      ) as Row | undefined;
      if (row === undefined) return null;
      const levels = parseDepthJson(text(row["depth_json"]));
      return { id: num(row["id"]) ?? 0, ticker: text(row["ticker"]), ts: text(row["ts"]), ...levels };
    });
    if (!r.ok) return r;
    if (r.value === null) return err("bad_request", `No recorded snapshot for ${ticker}.`);
    return { ok: true, value: r.value };
  }

  feed(path: string, q: DbFeedQuery): DbResult<DbFeedSeries> {
    const limit = Math.min(MAX_FEED_POINTS, Math.max(1, q.limit ?? MAX_FEED_POINTS));
    return this.run(path, (o) => {
      const rows = o.db
        .prepare(
          `SELECT known_at, value FROM feed_values
           WHERE feed = ? AND key = ? AND known_at >= ? AND known_at <= ?
           ORDER BY known_at LIMIT ?`,
        )
        .all(q.feed, q.key, unixToDbTime(q.from), unixToDbTime(q.to, true), limit + 1) as Row[];
      const truncated = rows.length > limit;
      const points = rows.slice(0, limit).map((r) => ({ knownAt: dbTimeToUnix(text(r["known_at"])), value: num(r["value"]) ?? Number.NaN }));
      return { feed: q.feed, key: q.key, points, truncated };
    });
  }

  close(): void {
    try {
      this.open?.db.close();
    } catch {
      // already closed
    }
    this.open = null;
  }
}
