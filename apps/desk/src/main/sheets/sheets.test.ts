// @vitest-environment node
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { parse } from "csv-parse/sync";
import { afterEach, describe, expect, it } from "vitest";
import { loadSource } from "../data/source";
import { assertAllowedUrl, parseSheetRef, SheetsClient, tabUrl, type SheetsFetch, type SheetsResponse } from "./client";
import { fixtureSheetsFetch } from "./fixtures";
import { checkTab, normalizeGvizCsv, parsePortfolios, SheetsManager, validateInterval } from "./sync";

/** No test touches the network: every fetch is a fake serving recorded-style CSV. */

// A made-up ID: never a real sheet.
const FAKE_ID = "1FAKEsheetIdForNemDeskTestsOnly_0000000000a";
const FIXTURES = resolve(import.meta.dirname, "../../../../../contract/fixtures");
const tmps: string[] = [];

function tmp(): string {
  const d = mkdtempSync(join(tmpdir(), "nem-desk-sheets-"));
  tmps.push(d);
  return d;
}

afterEach(() => {
  for (const d of tmps.splice(0)) rmSync(d, { recursive: true, force: true });
});

/** What gviz `out:csv` sends: every cell quoted, padded with empty columns and a blank row. */
function gviz(csv: string, padCols = 3): string {
  const records: string[][] = parse(csv, { bom: true, skip_empty_lines: true });
  const width = (records[0]?.length ?? 0) + padCols;
  const pad = (r: string[]) => [...r, ...Array<string>(width - r.length).fill("")];
  const rows = [...records.map(pad), pad([])];
  return `${rows.map((r) => r.map((c) => `"${c.replaceAll('"', '""')}"`).join(",")).join("\n")}\n`;
}

function res(status: number, body = "", headers: Record<string, string> = { "content-type": "text/csv; charset=utf-8" }): SheetsResponse {
  return {
    status,
    headers: { get: (n) => headers[n.toLowerCase()] ?? null },
    text: () => Promise.resolve(body),
  };
}

interface Call {
  url: string;
  init: Parameters<SheetsFetch>[1];
}

/** Serves `<fixture>/out/fixture/<tab>.csv` as gviz would; unknown tabs get the first tab (like Google). */
function fixtureFetch(fixture: string, tabs: Record<string, string> = {}): { fetch: SheetsFetch; calls: Call[] } {
  const calls: Call[] = [];
  const dir = join(FIXTURES, fixture, "out", "fixture");
  const fetch: SheetsFetch = (url, init) => {
    calls.push({ url, init });
    const tab = new URL(url).searchParams.get("sheet") ?? "";
    const file = tabs[tab] ?? (tab.startsWith("fixture ") ? `${tab.slice(8)}.csv` : "summary.csv");
    const path = join(dir, file);
    return Promise.resolve(res(200, gviz(readFileSync(existsSync(path) ? path : join(dir, "summary.csv"), "utf8"))));
  };
  return { fetch, calls };
}

function client(fetch: SheetsFetch): SheetsClient {
  return new SheetsClient({ fetch, userAgent: "NEM Desk/test", sleep: () => Promise.resolve(), perSecond: 1000 });
}

interface Harness {
  m: SheetsManager;
  dir: string;
  cache: string;
  /** Swap what "Google" answers from now on. */
  serve: (f: SheetsFetch) => void;
  synced: string[];
}

function manager(fetch: SheetsFetch, logs: string[] = []): Harness {
  const dir = tmp();
  let current = fetch;
  const synced: string[] = [];
  const m = new SheetsManager({
    storePath: join(dir, "sheets.json"),
    cacheDir: join(dir, "sheets-cache"),
    client: client((url, init) => current(url, init)),
    timers: false,
    log: (_l, msg) => logs.push(msg),
    onSynced: (d) => synced.push(d),
  });
  return { m, dir, cache: join(dir, "sheets-cache"), serve: (f) => (current = f), synced };
}

describe("parseSheetRef", () => {
  it("takes a share link or a bare ID", () => {
    expect(parseSheetRef(`https://docs.google.com/spreadsheets/d/${FAKE_ID}/edit#gid=0`)).toEqual({ ok: true, id: FAKE_ID });
    expect(parseSheetRef(`https://docs.google.com/spreadsheets/u/1/d/${FAKE_ID}/edit?usp=sharing`)).toEqual({ ok: true, id: FAKE_ID });
    expect(parseSheetRef(`  ${FAKE_ID} `)).toEqual({ ok: true, id: FAKE_ID });
  });

  it("refuses other hosts, http, publish-to-web links and junk", () => {
    for (const bad of [
      `http://docs.google.com/spreadsheets/d/${FAKE_ID}/edit`,
      `https://evil.example/spreadsheets/d/${FAKE_ID}/edit`,
      `https://docs.google.com.evil.example/spreadsheets/d/${FAKE_ID}/edit`,
      "https://docs.google.com/spreadsheets/d/e/2PACX-1vFAKE/pubhtml",
      "https://docs.google.com/document/d/abc/edit",
      "short",
      "",
      42,
    ]) {
      const r = parseSheetRef(bad);
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.error.field).toBe("sheet");
    }
  });
});

describe("URL allowlist", () => {
  it("builds only docs.google.com gviz CSV URLs", () => {
    const u = new URL(tabUrl(FAKE_ID, "fixture trades"));
    expect(u.origin).toBe("https://docs.google.com");
    expect(u.pathname).toBe(`/spreadsheets/d/${FAKE_ID}/gviz/tq`);
    expect(u.searchParams.get("tqx")).toBe("out:csv");
    expect(u.searchParams.get("sheet")).toBe("fixture trades");
  });

  it("rejects anything else", () => {
    expect(() => assertAllowedUrl("https://accounts.google.com/ServiceLogin")).toThrow();
    expect(() => assertAllowedUrl(`https://docs.google.com/spreadsheets/d/${FAKE_ID}/export?format=csv`)).toThrow();
    expect(() => assertAllowedUrl(`https://user:pw@docs.google.com/spreadsheets/d/${FAKE_ID}/gviz/tq`)).toThrow();
    expect(() => tabUrl("../../etc", "x")).toThrow();
  });
});

describe("SheetsClient", () => {
  it("sends a plain GET: no cookies, no auth, redirects not followed", async () => {
    const { fetch, calls } = fixtureFetch("basic");
    const r = await client(fetch).tab(FAKE_ID, "fixture summary");
    expect(r.ok).toBe(true);
    const call = calls[0];
    expect(call?.init.method).toBe("GET");
    expect(call?.init.redirect).toBe("manual");
    expect(call?.init.credentials).toBe("omit");
    const names = Object.keys(call?.init.headers ?? {}).map((h) => h.toLowerCase());
    expect(names).not.toContain("authorization");
    expect(names).not.toContain("cookie");
  });

  it("reports a private sheet (redirect to sign-in, 401/403, HTML page)", async () => {
    const cases: SheetsResponse[] = [
      res(302, "", { location: "https://accounts.google.com/ServiceLogin?continue=x" }),
      res(403),
      res(200, "<!DOCTYPE html><html>sign in</html>", { "content-type": "text/html; charset=utf-8" }),
    ];
    for (const c of cases) {
      const r = await client(() => Promise.resolve(c)).tab(FAKE_ID, "x summary");
      expect(r.ok ? null : r.error.kind).toBe("not_shared");
      if (!r.ok) expect(r.error.message).not.toContain(FAKE_ID);
    }
  });

  it("never follows a redirect elsewhere", async () => {
    let n = 0;
    const r = await client(() => {
      n++;
      return Promise.resolve(res(307, "", { location: "https://doc-08-sheets.googleusercontent.com/export/x" }));
    }).tab(FAKE_ID, "x summary");
    expect(r.ok ? null : r.error.kind).toBe("blocked");
    expect(n).toBe(1);
  });

  it("maps 404 to not_found, retries 429 / 5xx / network errors, then gives up", async () => {
    const nf = await client(() => Promise.resolve(res(404, "<html>", { "content-type": "text/html" }))).tab(FAKE_ID, "x summary");
    expect(nf.ok ? null : nf.error.kind).toBe("not_found");

    const seq = [res(429), res(503), res(200, '"a"\n"1"\n')];
    const ok = await client(() => Promise.resolve(seq.shift() ?? res(500))).tab(FAKE_ID, "x summary");
    expect(ok.ok).toBe(true);

    let tries = 0;
    const off = await client(() => {
      tries++;
      return Promise.reject(new Error("getaddrinfo ENOTFOUND"));
    }).tab(FAKE_ID, "x summary");
    expect(off.ok ? null : off.error.kind).toBe("offline");
    expect(tries).toBe(3);
  });
});

describe("gviz CSV handling", () => {
  it("drops padding columns and rows, keeps cell text", () => {
    const r = normalizeGvizCsv('"a","b","",""\n"1","","",""\n"","","",""\n');
    expect(r).toEqual({ ok: true, records: [["a", "b"], ["1", ""]] });
  });

  it("detects Google's first-tab fallback for a missing tab", () => {
    const summary = normalizeGvizCsv(gviz(readFileSync(join(FIXTURES, "basic/out/fixture/summary.csv"), "utf8")));
    if (!summary.ok) throw new Error("parse");
    expect(checkTab(summary.records, "fixture", "summary")).toBeNull();
    expect(checkTab(summary.records, "fixture", "trades")?.kind).toBe("missing_tab");
    // Same columns, another portfolio's rows: still the wrong tab.
    expect(checkTab(summary.records, "other", "summary")?.kind).toBe("missing_tab");
    expect(checkTab([["Student Name", "Gender"]], "fixture", "summary")?.kind).toBe("missing_tab");
  });
});

describe("config", () => {
  it("validates portfolios and the poll interval (default 5, min 1 minute)", () => {
    expect(parsePortfolios("btc, weather")).toEqual({ ok: true, names: ["btc", "weather"] });
    expect(parsePortfolios(["Bad Name"]).ok).toBe(false);
    expect(parsePortfolios(["backtests"]).ok).toBe(false);
    expect(parsePortfolios([]).ok).toBe(false);
    expect(validateInterval(undefined)).toEqual({ ok: true, minutes: 5 });
    expect(validateInterval(null)).toEqual({ ok: true, minutes: null });
    expect(validateInterval(1)).toEqual({ ok: true, minutes: 1 });
    expect(validateInterval(0.5).ok).toBe(false);
    expect(validateInterval(0).ok).toBe(false);
  });

  it("stores the ID only in its 0600 store file and shows a masked form", () => {
    const { fetch } = fixtureFetch("basic");
    const { m, dir } = manager(fetch);
    const r = m.save({ sheet: `https://docs.google.com/spreadsheets/d/${FAKE_ID}/edit`, portfolios: ["fixture"], intervalMinutes: 5 });
    expect(r.ok).toBe(true);
    const state = JSON.stringify(m.state());
    expect(state).not.toContain(FAKE_ID);
    expect(m.state().sheetMasked).toBe("1FAK…000a");
    const store = join(dir, "sheets.json");
    expect(readFileSync(store, "utf8")).toContain(FAKE_ID);
    if (process.platform !== "win32") expect(statSync(store).mode & 0o777).toBe(0o600);
    // An empty link keeps the saved sheet.
    expect(m.save({ sheet: "", portfolios: ["fixture"], intervalMinutes: 15 }).ok).toBe(true);
    expect(m.sheetId()).toBe(FAKE_ID);
  });
});

describe("SheetsManager sync", () => {
  it("yields the same entities as the folder CSVs (same coercion and validation)", async () => {
    const logs: string[] = [];
    const { fetch, calls } = fixtureFetch("basic");
    const { m, cache: dir, synced } = manager(fetch, logs);
    m.save({ sheet: FAKE_ID, portfolios: ["fixture"], intervalMinutes: null });
    const info = await m.sync();
    expect(info.ok).toBe(true);
    expect(info.tabs.map((t) => `${t.tab}:${String(t.ok)}`)).toEqual(["summary:true", "strategies:true", "trades:true", "equity:true"]);
    expect(calls.map((c) => new URL(c.url).searchParams.get("sheet"))).toEqual(["fixture summary", "fixture strategies", "fixture trades", "fixture equity"]);
    expect(synced).toEqual([dir]);

    const fromSheet = loadSource(dir);
    const fromFolder = loadSource(join(FIXTURES, "basic"));
    expect(fromSheet.portfolios.map((p) => p.errors)).toEqual([[]]);
    const pick = (s: typeof fromSheet) => s.portfolios.map(({ name, summary, strategies, trades, equity }) => ({ name, summary, strategies, trades, equity }));
    expect(pick(fromSheet)).toEqual(pick(fromFolder));
    // Nothing logged or reported carries the ID.
    expect([...logs, JSON.stringify(info)].join("\n")).not.toContain(FAKE_ID);
    expect(readdirSync(dir)).toEqual(["out"]);
  });

  it("a missing tab is reported and its stale copy removed", async () => {
    const good = fixtureFetch("basic");
    const { m, cache: dir, serve } = manager(good.fetch);
    m.save({ sheet: FAKE_ID, portfolios: ["fixture"], intervalMinutes: null });
    await m.sync();
    expect(existsSync(join(dir, "out/fixture/trades.csv"))).toBe(true);

    // Swap in a fetch that has no trades tab: Google answers with the first tab instead.
    const missing = fixtureFetch("basic", { "fixture trades": "summary.csv" });
    serve(missing.fetch);
    const info = await m.sync();
    expect(info.ok).toBe(false);
    expect(info.tabs.find((t) => t.tab === "trades")?.error?.kind).toBe("missing_tab");
    expect(existsSync(join(dir, "out/fixture/trades.csv"))).toBe(false);
    expect(loadSource(dir).portfolios[0]?.errors.map((e) => e.kind)).toEqual(["missing_file"]);
  });

  it("a missing equity tab (older engine) is not an error and leaves no stale copy", async () => {
    const good = fixtureFetch("basic");
    const { m, cache: dir, serve } = manager(good.fetch);
    m.save({ sheet: FAKE_ID, portfolios: ["fixture"], intervalMinutes: null });
    await m.sync();
    expect(existsSync(join(dir, "out/fixture/equity.csv"))).toBe(true);

    serve(fixtureFetch("basic", { "fixture equity": "summary.csv" }).fetch);
    const info = await m.sync();
    expect(info.ok).toBe(true);
    expect(info.tabs.map((t) => t.tab)).toEqual(["summary", "strategies", "trades"]);
    expect(existsSync(join(dir, "out/fixture/equity.csv"))).toBe(false);
    expect(loadSource(dir).portfolios[0]?.errors).toEqual([]);
  });

  it("schema problems come back per tab and the data layer shows the same error", async () => {
    const { fetch } = fixtureFetch("broken/wrong_major_version");
    const { m, cache: dir } = manager(fetch);
    m.save({ sheet: FAKE_ID, portfolios: ["fixture"], intervalMinutes: null });
    const info = await m.sync();
    expect(info.ok).toBe(false);
    expect(info.tabs.some((t) => t.error?.kind === "schema")).toBe(true);
    expect(loadSource(dir).portfolios[0]?.errors.some((e) => e.kind === "version")).toBe(true);
  });

  it("stops on a private sheet and keeps the last good cache", async () => {
    const good = fixtureFetch("basic");
    const { m, cache: dir, serve } = manager(good.fetch);
    m.save({ sheet: FAKE_ID, portfolios: ["fixture"], intervalMinutes: null });
    await m.sync();
    let n = 0;
    serve(() => {
      n++;
      return Promise.resolve(res(302, "", { location: "https://accounts.google.com/ServiceLogin" }));
    });
    const info = await m.sync();
    expect(info.error?.kind).toBe("not_shared");
    expect(n).toBe(1);
    expect(loadSource(dir).portfolios[0]?.summary?.portfolio).toBe("fixture");
  });

  it("forget removes the stored ID and the cache", async () => {
    const { fetch } = fixtureFetch("basic");
    const { m, dir } = manager(fetch);
    m.save({ sheet: FAKE_ID, portfolios: ["fixture"], intervalMinutes: null });
    await m.sync();
    m.forget();
    expect(m.state().configured).toBe(false);
    expect(readFileSync(join(dir, "sheets.json"), "utf8")).not.toContain(FAKE_ID);
    expect(existsSync(join(dir, "sheets-cache"))).toBe(false);
  });

  it("not configured is an error value", async () => {
    const { m } = manager(fixtureFetch("basic").fetch);
    expect((await m.sync()).error?.kind).toBe("not_configured");
  });
});

describe("e2e fixture transport", () => {
  it("serves <tab>.csv and falls back to the first tab like gviz", async () => {
    const dir = join(FIXTURES, "basic/out/fixture");
    const f = fixtureSheetsFetch(dir);
    const init = { method: "GET", headers: {}, redirect: "manual", credentials: "omit" } as const;
    const a = await f(tabUrl(FAKE_ID, "strategies"), init);
    expect((await a.text()).startsWith("schema_version")).toBe(true);
    const b = await f(tabUrl(FAKE_ID, "nope"), init);
    expect(await b.text()).toBe(readFileSync(join(dir, "strategies.csv"), "utf8"));
  });
});
