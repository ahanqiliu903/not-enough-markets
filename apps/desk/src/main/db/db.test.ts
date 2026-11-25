// @vitest-environment node
import { chmodSync, copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, describe, expect, it } from "vitest";
import { dbTimeToUnix } from "@shared/db";
import { SourceManager, dbPathFor } from "../data/sources";
import { parseFeedQuery, parseMarketsQuery } from "./handlers";
import { DbReader, parseDepthJson, unixToDbTime } from "./reader";

const BASIC = resolve(import.meta.dirname, "../../../../../contract/fixtures/basic");
const DB = join(BASIC, "nem.db");
const tmps: string[] = [];
const readers: DbReader[] = [];

function tmp(): string {
  const d = mkdtempSync(join(tmpdir(), "nem-desk-db-"));
  tmps.push(d);
  return d;
}

function reader(): DbReader {
  const r = new DbReader();
  readers.push(r);
  return r;
}

afterEach(() => {
  for (const r of readers.splice(0)) r.close();
  for (const d of tmps.splice(0)) {
    try {
      chmodSync(d, 0o755);
    } catch {
      // already gone
    }
    rmSync(d, { recursive: true, force: true });
  }
});

function makeDb(path: string, setup: (db: DatabaseSync) => void): void {
  const db = new DatabaseSync(path);
  setup(db);
  db.close();
}

describe("time helpers", () => {
  it("parses the engine's isoformat with microseconds and +00:00", () => {
    expect(dbTimeToUnix("2026-10-01T00:15:00.000000+00:00")).toBe(Date.UTC(2026, 9, 1, 0, 15) / 1000);
    expect(dbTimeToUnix("2026-10-01T00:15:00.250000+00:00")).toBeCloseTo(Date.UTC(2026, 9, 1, 0, 15) / 1000 + 0.25);
    expect(dbTimeToUnix("2026-10-01T00:15:00Z")).toBe(Date.UTC(2026, 9, 1, 0, 15) / 1000);
    expect(Number.isNaN(dbTimeToUnix("yesterday"))).toBe(true);
  });

  it("formats range bounds the way the engine stores text, so they compare", () => {
    expect(unixToDbTime(Date.UTC(2026, 9, 1) / 1000)).toBe("2026-10-01T00:00:00.000000+00:00");
    expect(unixToDbTime(Date.UTC(2026, 9, 1) / 1000, true)).toBe("2026-10-01T00:00:00.000999+00:00");
  });

  it("parses depth_json defensively", () => {
    expect(parseDepthJson('{"yes": [[0.2, 50.0]], "no": [[0.79, 5]]}')).toEqual({ yes: [[0.2, 50]], no: [[0.79, 5]] });
    expect(parseDepthJson("not json")).toEqual({ yes: [], no: [] });
    expect(parseDepthJson('{"yes": [["x", 1], [0.1]]}')).toEqual({ yes: [], no: [] });
  });
});

describe("DbReader on contract/fixtures/basic/nem.db", () => {
  it("reads series, feeds and the schema version", () => {
    const r = reader().info(DB);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.value.schemaVersion).toBe(9);
    expect(r.value.series.map((s) => s.series)).toEqual(["KXBTC15M", "KXETH15M", "KXSOL15M"]);
    expect(r.value.series[0]?.snapshots).toBe(2550);
    expect(r.value.feeds).toEqual([
      expect.objectContaining({ feed: "btc_spot", key: "BTC-USD", values: 2550 }),
    ]);
    expect(r.value.newestTs).toBe("2026-10-02T18:29:00.000000+00:00");
  });

  it("pages markets newest first with their recorded settlement", () => {
    const rd = reader();
    const r = rd.markets(DB, { series: "KXBTC15M", offset: 0, limit: 5 });
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.value.total).toBe(170);
    expect(r.value.rows).toHaveLength(5);
    const [a, b] = r.value.rows;
    expect(a !== undefined && b !== undefined && a.closeTime >= b.closeTime).toBe(true);
    expect(a?.snapshots).toBe(15);
    expect(["yes", "no"]).toContain(a?.result);
    const next = rd.markets(DB, { series: "KXBTC15M", offset: 5, limit: 5 });
    expect(next.ok && next.value.rows[0]?.ticker).not.toBe(a?.ticker);
    const filtered = rd.markets(DB, { series: "KXBTC15M", text: a?.ticker.slice(-12) ?? "", offset: 0, limit: 50 });
    expect(filtered.ok && filtered.value.rows.map((m) => m.ticker)).toContain(a?.ticker);
    // LIKE wildcards in the filter are literal.
    const none = rd.markets(DB, { series: "KXBTC15M", text: "%", offset: 0, limit: 5 });
    expect(none.ok && none.value.total).toBe(0);
  });

  it("returns the top-of-book series and depth as recorded", () => {
    const rd = reader();
    const top = rd.top(DB, "KXBTC15M-26OCT010015-00");
    expect(top.ok).toBe(true);
    if (!top.ok) return;
    expect(top.value.points).toHaveLength(15);
    expect(top.value.truncated).toBe(false);
    const first = top.value.points[0];
    expect(first).toMatchObject({ yesBid: 0.2, yesAsk: 0.21, noBid: 0.79, noAsk: 0.8 });
    expect(first?.t).toBe(Date.UTC(2026, 9, 1) / 1000);
    const depth = rd.depth(DB, "KXBTC15M-26OCT010015-00", first?.id);
    expect(depth.ok && depth.value.yes).toEqual([
      [0.2, 50],
      [0.19, 60],
      [0.18, 70],
    ]);
    const latest = rd.depth(DB, "KXBTC15M-26OCT010015-00");
    expect(latest.ok && latest.value.id).toBe(top.value.points.at(-1)?.id);
    const missing = rd.depth(DB, "KXNOPE-1");
    expect(missing.ok ? null : missing.error.kind).toBe("bad_request");
  });

  it("reads feed values by known_at within a range", () => {
    const from = Date.UTC(2026, 9, 1, 0, 0) / 1000;
    const r = reader().feed(DB, { feed: "btc_spot", key: "BTC-USD", from, to: from + 14 * 60 });
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.value.points).toHaveLength(15);
    expect(r.value.points[0]?.knownAt).toBe(from);
    const capped = reader().feed(DB, { feed: "btc_spot", key: "BTC-USD", from, to: from + 3600, limit: 10 });
    expect(capped.ok && capped.value.truncated).toBe(true);
  });

  it("never writes: the fixture is byte-identical after reading", () => {
    const before = statSync(DB).mtimeMs;
    const bytes = readFileSync(DB);
    const rd = reader();
    rd.info(DB);
    rd.markets(DB, { series: "KXETH15M", offset: 0, limit: 10 });
    rd.close();
    expect(statSync(DB).mtimeMs).toBe(before);
    expect(readFileSync(DB).equals(bytes)).toBe(true);
  });
});

describe("DbReader errors are values", () => {
  it("missing file", () => {
    const r = reader().info(join(tmp(), "nope.db"));
    expect(r.ok ? null : r.error.kind).toBe("missing_file");
  });

  it("not an SQLite file", () => {
    const p = join(tmp(), "junk.db");
    writeFileSync(p, "this is not a database ".repeat(20));
    const r = reader().info(p);
    expect(r.ok ? null : r.error.kind).toBe("not_sqlite");
  });

  it("wrong schema version names expected and found", () => {
    const p = join(tmp(), "v10.db");
    makeDb(p, (db) => db.exec("CREATE TABLE snapshots (id INTEGER); PRAGMA user_version = 10;"));
    const r = reader().info(p);
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.error).toMatchObject({ kind: "schema_version", expected: "3, 4, 5, 6, 7, 8, 9", found: "10" });
  });

  it("refuses a live database (a -wal file beside it)", () => {
    const d = tmp();
    const p = join(d, "nem.db");
    copyFileSync(DB, p);
    writeFileSync(`${p}-wal`, "");
    const r = reader().info(p);
    expect(r.ok ? null : r.error.kind).toBe("live_db");
  });

  it("a missing table is a query error, not a crash", () => {
    const p = join(tmp(), "v3-empty.db");
    makeDb(p, (db) => db.exec("PRAGMA user_version = 3;"));
    const r = reader().info(p);
    expect(r.ok ? null : r.error.kind).toBe("query");
  });

  it("opens a WAL-mode snapshot in a read-only folder without creating files", () => {
    const d = tmp();
    const live = join(d, "live.db");
    makeDb(live, (db) => {
      db.exec("PRAGMA journal_mode = WAL; CREATE TABLE t (a); PRAGMA user_version = 3;");
    });
    const snapDir = join(d, "snapshot");
    mkdirSync(snapDir);
    const snap = join(snapDir, "nem.db");
    makeDb(live, (db) => db.exec(`VACUUM INTO '${snap}'`));
    makeDb(snap, (db) => db.exec("PRAGMA journal_mode = WAL;"));
    rmSync(`${snap}-wal`, { force: true });
    rmSync(`${snap}-shm`, { force: true });
    chmodSync(snapDir, 0o555);
    const r = reader().info(snap);
    // Schema check passes; the missing tables surface as a query error, not a lock/open failure.
    expect(r.ok ? null : r.error.kind).toBe("query");
    chmodSync(snapDir, 0o755);
    expect(() => statSync(`${snap}-shm`)).toThrow();
  });

  it("reopens a file replaced atomically (rsync / snapshot.sh rename)", () => {
    const d = tmp();
    const p = join(d, "nem.db");
    copyFileSync(DB, p);
    const rd = reader();
    expect(rd.info(p).ok).toBe(true);
    const next = join(d, "next.db");
    makeDb(next, (db) => db.exec("PRAGMA user_version = 10;"));
    copyFileSync(next, `${p}.tmp`);
    rmSync(p);
    copyFileSync(`${p}.tmp`, p);
    const r = rd.info(p);
    expect(r.ok ? null : r.error.kind).toBe("schema_version");
  });
});

describe("request validation", () => {
  it("markets query", () => {
    expect(parseMarketsQuery({ series: "KXBTC15M" })).toEqual({ series: "KXBTC15M", offset: 0, limit: 200 });
    expect(parseMarketsQuery({ series: "KX; DROP", limit: 1e6 })).toBeNull();
    expect(parseMarketsQuery({ series: "KXBTC15M", limit: 1e6 })?.limit).toBe(500);
  });

  it("feed query", () => {
    expect(parseFeedQuery({ feed: "btc_spot", key: "BTC-USD", from: 1, to: 2 })).toEqual({ feed: "btc_spot", key: "BTC-USD", from: 1, to: 2 });
    expect(parseFeedQuery({ feed: "btc_spot", key: "BTC-USD", from: 2, to: 1 })).toBeNull();
    expect(parseFeedQuery({ feed: 1, key: "x", from: 1, to: 2 })).toBeNull();
  });
});

describe("sources and databases", () => {
  it("finds nem.db or data/snapshot/nem.db in a source folder, or uses the attached file", () => {
    const d = tmp();
    expect(dbPathFor({ id: "a", label: "a", kind: "folder", path: d })).toBeNull();
    mkdirSync(join(d, "data", "snapshot"), { recursive: true });
    writeFileSync(join(d, "data", "snapshot", "nem.db"), "");
    expect(dbPathFor({ id: "a", label: "a", kind: "folder", path: d })).toBe(join(d, "data", "snapshot", "nem.db"));
    // The live data/nem.db is never picked up on its own.
    writeFileSync(join(d, "data", "nem.db"), "");
    expect(dbPathFor({ id: "a", label: "a", kind: "folder", path: d })).toBe(join(d, "data", "snapshot", "nem.db"));
    expect(dbPathFor({ id: "a", label: "a", kind: "sample", path: BASIC })).toBe(DB);
    expect(dbPathFor({ id: "a", label: "a", kind: "folder", path: d, dbPath: "/x/y.db" })).toBe("/x/y.db");
  });

  it("attaches, persists and detaches a database; a DB change bumps the revision", () => {
    const d = tmp();
    const store = join(d, "sources.json");
    const folder = join(d, "src");
    mkdirSync(folder);
    const db = join(d, "snap.db");
    copyFileSync(DB, db);
    const m = new SourceManager({ storePath: store, samplePath: BASIC, watch: false });
    const added = m.add(folder);
    expect(added.ok).toBe(true);
    if (!added.ok) return;
    expect(m.activeDbPath()).toBeNull();
    const att = m.attachDb(added.source.id, db);
    expect(att.ok && att.state.dbPaths?.[added.source.id]).toBe(db);
    expect(m.attachDb(added.source.id, "relative.db").ok).toBe(false);
    expect(m.attachDb("nope", db).ok).toBe(false);
    const reloaded = new SourceManager({ storePath: store, samplePath: BASIC, watch: false });
    expect(reloaded.activeDbPath()).toBe(db);
    writeFileSync(db, readFileSync(DB));
    const t = new Date(Date.now() + 5000);
    utimesSync(db, t, t);
    expect(m.checkForChanges()).toBe(true);
    expect(m.detachDb(added.source.id).dbPaths?.[added.source.id]).toBeNull();
    m.dispose();
    reloaded.dispose();
  });

  it("upserts a managed source (VPS cache) under a fixed id", () => {
    const d = tmp();
    const cache = join(d, "cache");
    mkdirSync(cache);
    const m = new SourceManager({ storePath: join(d, "s.json"), samplePath: null, watch: false });
    const a = m.upsertManaged({ id: "vps", label: "VPS a@b", kind: "vps", path: cache });
    expect(a.ok && a.state.activeId).toBe("vps");
    const b = m.upsertManaged({ id: "vps", label: "VPS c@d", kind: "vps", path: cache });
    expect(b.ok && b.state.sources.filter((s) => s.id === "vps")).toHaveLength(1);
    expect(b.ok && b.source.label).toBe("VPS c@d");
    expect(m.upsertManaged({ id: "vps", label: "x", kind: "vps", path: join(d, "missing") }).ok).toBe(false);
    m.dispose();
  });
});
