import { BrowserWindow, dialog } from "electron";
import type { DbError, DbFeedQuery, DbMarketsQuery, DbResult } from "@shared/db";
import type { SourceResult } from "@shared/data";
import type { IpcChannels } from "@shared/ipc";
import { sources } from "../data/handlers";
import { checkDbFile, DbReader, MAX_MARKETS_PAGE } from "./reader";

/**
 * `db:*` IPC handlers (DESK-010): read-only queries on the active source's snapshot
 * database. Requests come from the renderer, so every field is checked here.
 */

const reader = new DbReader();

function bad(message: string): { ok: false; error: DbError } {
  return { ok: false, error: { kind: "bad_request", message } };
}

const TICKER = /^[A-Za-z0-9._-]{1,96}$/;
const NAME = /^[\w.:/@ -]{1,128}$/;

function obj(req: unknown): Record<string, unknown> {
  return typeof req === "object" && req !== null ? (req as Record<string, unknown>) : {};
}

function int(v: unknown, lo: number, hi: number, d: number): number {
  return typeof v === "number" && Number.isInteger(v) ? Math.min(hi, Math.max(lo, v)) : d;
}

export function parseMarketsQuery(req: unknown): DbMarketsQuery | null {
  const r = obj(req);
  const series = r["series"];
  if (typeof series !== "string" || !TICKER.test(series)) return null;
  const q: DbMarketsQuery = {
    series,
    offset: int(r["offset"], 0, 1e9, 0),
    limit: int(r["limit"], 1, MAX_MARKETS_PAGE, 200),
  };
  const text = r["text"];
  if (typeof text === "string" && text.trim() !== "" && text.length <= 96) q.text = text.trim();
  return q;
}

export function parseFeedQuery(req: unknown): DbFeedQuery | null {
  const r = obj(req);
  const { feed, key, from, to, limit } = r;
  if (typeof feed !== "string" || !NAME.test(feed) || typeof key !== "string" || !NAME.test(key)) return null;
  if (typeof from !== "number" || typeof to !== "number" || !Number.isFinite(from) || !Number.isFinite(to) || to < from) {
    return null;
  }
  const q: DbFeedQuery = { feed, key, from, to };
  if (typeof limit === "number" && Number.isInteger(limit) && limit > 0) q.limit = limit;
  return q;
}

function withDb<T>(fn: (path: string) => DbResult<T>): DbResult<T> {
  const path = sources().activeDbPath();
  if (path === null) {
    const active = sources().active();
    return {
      ok: false,
      error: {
        kind: "no_db",
        message:
          active === null
            ? "No data source is connected."
            : `${active.label} has no database. Attach a snapshot (nem.db) in Sources and Settings.`,
      },
    };
  }
  return fn(path);
}

type DbChannel = Extract<keyof IpcChannels, `db:${string}`>;
type DbHandlers = {
  [C in DbChannel]: (
    request: IpcChannels[C]["request"],
  ) => IpcChannels[C]["response"] | Promise<IpcChannels[C]["response"]>;
};

export const dbHandlers: DbHandlers = {
  "db:info": () => withDb((p) => reader.info(p)),
  "db:markets": (req) => {
    const q = parseMarketsQuery(req);
    return q === null ? bad("Invalid series.") : withDb((p) => reader.markets(p, q));
  },
  "db:top": (req) => {
    const t: unknown = req;
    return typeof t === "string" && TICKER.test(t) ? withDb((p) => reader.top(p, t)) : bad("Invalid ticker.");
  },
  "db:depth": (req) => {
    const r = obj(req);
    const ticker = r["ticker"];
    const id = r["id"];
    if (typeof ticker !== "string" || !TICKER.test(ticker)) return bad("Invalid ticker.");
    if (id !== undefined && !(typeof id === "number" && Number.isInteger(id) && id > 0)) return bad("Invalid snapshot id.");
    return withDb((p) => reader.depth(p, ticker, id));
  },
  "db:feed": (req) => {
    const q = parseFeedQuery(req);
    return q === null ? bad("Invalid feed query.") : withDb((p) => reader.feed(p, q));
  },
  "db:attach": async (req): Promise<SourceResult> => {
    const r = obj(req);
    const id = r["id"];
    let path = r["path"];
    if (path === undefined) {
      const win = BrowserWindow.getFocusedWindow();
      const opts = {
        title: "Choose a snapshot database (data/snapshot/nem.db)",
        properties: ["openFile" as const],
        filters: [{ name: "SQLite database", extensions: ["db", "sqlite", "sqlite3"] }],
      };
      const picked = win !== null ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
      path = picked.filePaths[0];
      if (picked.canceled || path === undefined) return { ok: false, state: sources().state(), message: "cancelled" };
    }
    if (typeof path === "string") {
      const problem = checkDbFile(path);
      if (problem !== null) return { ok: false, state: sources().state(), message: problem.message };
    }
    reader.close();
    return sources().attachDb(id, path);
  },
  "db:detach": (req) => {
    reader.close();
    return sources().detachDb(obj(req)["id"]);
  },
};
