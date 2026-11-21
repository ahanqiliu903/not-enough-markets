// @vitest-environment node
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { TRADE_COLUMNS } from "@shared/data";
import { coerce, parseTable } from "./csv";
import { loadSource } from "./source";
import { SourceManager } from "./sources";
import { pnlCurves, TradeIndex, tradesCsv } from "./trades";

const FIXTURES = resolve(import.meta.dirname, "../../../../../contract/fixtures");
const BASIC = join(FIXTURES, "basic");
const tmps: string[] = [];

function tmp(): string {
  const d = mkdtempSync(join(tmpdir(), "nem-desk-"));
  tmps.push(d);
  return d;
}

afterEach(() => {
  for (const d of tmps.splice(0)) rmSync(d, { recursive: true, force: true });
});

describe("coerce (rules of tests/contract/test_contract.py)", () => {
  it("empty is null only when the schema allows null", () => {
    expect(coerce("", ["number", "null"])).toBeNull();
    expect(coerce("", "number")).toBe("");
    expect(coerce("", "string")).toBe("");
  });
  it("integer and number by declared type; unparseable stays text", () => {
    expect(coerce("5", "integer")).toBe(5);
    expect(coerce("5.0", "integer")).toBe("5.0");
    expect(coerce("0.25", ["number", "null"])).toBe(0.25);
    expect(coerce("1e3", "number")).toBe(1000);
    expect(coerce("abc", "number")).toBe("abc");
    expect(coerce("1.0", "string")).toBe("1.0");
    expect(coerce("x", undefined)).toBe("x");
  });
});

describe("basic fixture", () => {
  const src = loadSource(BASIC);

  it("loads with no errors", () => {
    expect(src.errors).toEqual([]);
    expect(src.portfolios.map((p) => [p.name, p.errors])).toEqual([["fixture", []]]);
  });

  it("has the portfolio summary, three verdicts and the trades", () => {
    const p = src.portfolios[0];
    expect(p?.summary?.equity_usd).toBe(1059.14);
    expect(p?.summary?.pnl_vs_cash_usd).toBe(58.95);
    expect(p?.strategies?.map((s) => [s.strategy, s.verdict]).sort()).toEqual([
      ["btc_edge", "edge"],
      ["eth_no_edge", "no_edge"],
      ["sol_undecided", "undecided"],
    ]);
    expect(p?.tradeCount).toBe(394);
    expect(p?.trades?.[0]?.pnl_usd).toBe(0.51);
  });

  it("reads summary.json extras, status.json and backtests", () => {
    expect(Object.keys(src.extras).sort()).toEqual([
      "fixture/btc_edge",
      "fixture/eth_no_edge",
      "fixture/sol_undecided",
    ]);
    expect(src.extras["fixture/btc_edge"]?.calibration.length).toBeGreaterThan(0);
    expect(src.status?.halts.length).toBeGreaterThan(0);
    expect(src.backtests.map((b) => b.status)).toEqual(["suggested"]);
    expect(src.newestAsOf).toMatch(/^2026-10-02T/);
  });

  it("also accepts the out/ folder itself (JSON files from its parent)", () => {
    const s = loadSource(join(BASIC, "out"));
    expect(s.errors).toEqual([]);
    expect(s.portfolios.map((p) => p.name)).toEqual(["fixture"]);
    expect(s.hasSummaryJson).toBe(true);
    expect(s.status).not.toBeNull();
  });

  it("P&L curve is the running sum of each strategy's settled pnl", () => {
    const { curves } = pnlCurves(src, [{ portfolio: "fixture", strategy: "btc_edge" }]);
    const c = curves[0];
    const row = src.portfolios[0]?.strategies?.find((s) => s.strategy === "btc_edge");
    expect(c?.pnl.length).toBe(row?.trades);
    // The engine's pnl_usd is the same sum, rounded to cents.
    expect(c?.pnl.at(-1)).toBeCloseTo(row?.pnl_usd ?? NaN, 1);
    expect(c?.t.every((t, i, a) => i === 0 || t >= (a[i - 1] ?? 0))).toBe(true);
  });
});

describe("empty fixture", () => {
  it("loads: one portfolio, no strategies, no trades, nulls stay null", () => {
    const src = loadSource(join(FIXTURES, "empty"));
    expect(src.errors).toEqual([]);
    const p = src.portfolios[0];
    expect(p?.errors).toEqual([]);
    expect(p?.strategies).toEqual([]);
    expect(p?.tradeCount).toBe(0);
    expect(p?.summary?.started_at).toBeNull();
    expect(p?.summary?.pnl_vs_cash_usd).toBeNull();
  });
});

describe("broken fixtures produce structured errors", () => {
  const load = (c: string) => loadSource(join(FIXTURES, "broken", c)).portfolios[0];

  it("missing_column blocks strategies.csv and names the column", () => {
    const p = load("missing_column");
    expect(p?.strategies).toBeNull();
    expect(p?.summary).not.toBeNull();
    expect(p?.errors).toEqual([
      expect.objectContaining({
        kind: "missing_column",
        file: "out/fixture/strategies.csv",
        field: "edge_cents",
        message: "missing columns ['edge_cents']",
      }),
    ]);
  });

  it("wrong_major_version names expected and found versions", () => {
    const p = load("wrong_major_version");
    expect(p?.strategies).toBeNull();
    const e = p?.errors[0];
    expect(e).toMatchObject({ kind: "version", file: "out/fixture/strategies.csv", row: 1, field: "schema_version", expected: "1.x", found: "2.0" });
    expect(e?.message).toContain("schema_version 2.0, expected 1.x");
    expect(p?.errors).toHaveLength(3);
  });

  it("bad_timestamp names the row and field", () => {
    const p = load("bad_timestamp");
    expect(p?.trades).toBeNull();
    expect(p?.tradeCount).toBeNull();
    expect(p?.strategies).not.toBeNull();
    expect(p?.errors).toEqual([
      expect.objectContaining({ kind: "invalid", file: "out/fixture/trades.csv", row: 1, field: "opened_at" }),
    ]);
    expect(p?.errors[0]?.message).toContain("must match pattern");
  });

  it("duplicate_rows is reported", () => {
    const p = load("duplicate_rows");
    expect(p?.trades).toBeNull();
    expect(p?.errors).toEqual([expect.objectContaining({ kind: "duplicate", file: "out/fixture/trades.csv", row: 395 })]);
    expect(p?.errors[0]?.message).toContain("duplicate rows: 2 row(s)");
  });

  it("a deleted file is an error, not a crash", () => {
    const d = tmp();
    cpSync(BASIC, d, { recursive: true });
    rmSync(join(d, "out/fixture/trades.csv"));
    const p = loadSource(d).portfolios[0];
    expect(p?.strategies?.length).toBe(3);
    expect(p?.errors).toEqual([expect.objectContaining({ kind: "missing_file", file: "out/fixture/trades.csv" })]);
  });

  it("finds summary.json and status.json inside out/ (where nem report writes them)", () => {
    const d = tmp();
    cpSync(BASIC, d, { recursive: true });
    for (const f of ["summary.json", "status.json"]) {
      writeFileSync(join(d, "out", f), readFileSync(join(d, f)));
      rmSync(join(d, f));
    }
    const s = loadSource(d);
    expect(s.errors).toEqual([]);
    expect(s.hasSummaryJson).toBe(true);
    expect(s.status?.halts.length).toBeGreaterThan(0);
  });

  it("a wrong major in summary.json blocks only that file", () => {
    const d = tmp();
    cpSync(BASIC, d, { recursive: true });
    const doc = JSON.parse(readFileSync(join(d, "summary.json"), "utf8")) as Record<string, unknown>;
    writeFileSync(join(d, "summary.json"), JSON.stringify({ ...doc, schema_version: "2.0" }));
    const s = loadSource(d);
    expect(s.hasSummaryJson).toBe(false);
    expect(s.errors).toEqual([expect.objectContaining({ kind: "version", file: "summary.json", found: "2.0" })]);
    expect(s.portfolios[0]?.strategies?.length).toBe(3);
  });

  it("a missing source folder is a source error", () => {
    expect(loadSource("/nonexistent/nem").errors[0]?.kind).toBe("source");
  });

  it("garbage CSV is a parse error", () => {
    const t = parseTable('a,"b\n', "x.csv", "trade_row.schema.json");
    expect(t.rows).toBeNull();
    expect(t.errors[0]?.kind).toBe("parse");
  });
});

describe("trades paging", () => {
  const src = loadSource(BASIC);

  it("filters, sorts (nulls last) and pages", () => {
    const idx = new TradeIndex();
    const all = idx.page(src, 1, { offset: 0, limit: 10 });
    expect(all.total).toBe(394);
    expect(all.rows).toHaveLength(10);
    const lost = idx.page(src, 1, { result: "lost", sort: { column: "pnl_usd", dir: "asc" }, offset: 0, limit: 1000 });
    expect(lost.rows.every((r) => r.result === "lost")).toBe(true);
    const pnls = lost.rows.map((r) => r.pnl_usd ?? 0);
    expect(pnls).toEqual([...pnls].sort((a, b) => a - b));
    const btc = idx.page(src, 1, { strategy: "btc_edge", text: "26oct01", offset: 0, limit: 1000 });
    expect(btc.rows.every((r) => r.strategy === "btc_edge" && r.ticker.includes("26OCT01"))).toBe(true);
    expect(btc.total).toBeGreaterThan(0);
  });

  it("CSV export keeps the engine's cell text", () => {
    const idx = new TradeIndex();
    const { refs } = idx.query(src, 1, { strategy: "eth_no_edge" });
    const csv = tradesCsv(refs).trim().split("\n");
    expect(csv[0]).toBe(TRADE_COLUMNS.join(","));
    expect(csv).toHaveLength(refs.length + 1);
    const original = readFileSync(join(BASIC, "out/fixture/trades.csv"), "utf8").split(/\r?\n/);
    expect(original).toContain(csv[1]);
  });

  it("blocked trades.csv returns errors instead of rows", () => {
    const broken = loadSource(join(FIXTURES, "broken", "bad_timestamp"));
    const r = new TradeIndex().page(broken, 1, { offset: 0, limit: 10 });
    expect(r.rows).toEqual([]);
    expect(r.errors[0]?.field).toBe("opened_at");
  });

  it("100k trades load and query fast", () => {
    const d = tmp();
    const dir = join(d, "out", "big");
    mkdirSync(dir, { recursive: true });
    const base = join(BASIC, "out", "fixture");
    writeFileSync(join(dir, "summary.csv"), readFileSync(join(base, "summary.csv"), "utf8").replaceAll("fixture", "big"));
    writeFileSync(join(dir, "strategies.csv"), readFileSync(join(base, "strategies.csv"), "utf8").replaceAll("fixture", "big"));
    const lines = [TRADE_COLUMNS.join(",")];
    for (let i = 0; i < 100_000; i++) {
      const h = String(i % 24).padStart(2, "0");
      const m = String(i % 60).padStart(2, "0");
      const won = i % 10 !== 0;
      lines.push(
        `1.0,big,s${i % 4},W${i},T${i}-00,yes,5,${88 + (i % 5)}.0,0.04,89.8,paper,2026-10-01T${h}:${m}:00Z,2026-10-01T${h}:${m}:30Z,2026-10-01T${h}:${m}:33Z,${won ? "won" : "lost"},${won ? "0.51" : "-4.49"}`,
      );
    }
    writeFileSync(join(dir, "trades.csv"), `${lines.join("\n")}\n`);

    let t0 = performance.now();
    const big = loadSource(d);
    const loadMs = performance.now() - t0;
    expect(big.portfolios[0]?.errors).toEqual([]);
    expect(big.portfolios[0]?.tradeCount).toBe(100_000);
    expect(loadMs).toBeLessThan(10_000);

    const idx = new TradeIndex();
    t0 = performance.now();
    const sorted = idx.page(big, 1, { sort: { column: "pnl_usd", dir: "desc" }, offset: 0, limit: 200 });
    const sortMs = performance.now() - t0;
    expect(sorted.total).toBe(100_000);
    expect(sortMs).toBeLessThan(1000);
    // Scrolling reuses the cached index: a slice.
    t0 = performance.now();
    const deep = idx.page(big, 1, { sort: { column: "pnl_usd", dir: "desc" }, offset: 90_000, limit: 200 });
    expect(performance.now() - t0).toBeLessThan(20);
    expect(deep.rows).toHaveLength(200);
  });
});

describe("SourceManager", () => {
  it("adds, persists, switches and removes folder sources", () => {
    const d = tmp();
    const store = join(d, "user", "sources.json");
    const m = new SourceManager({ storePath: store, samplePath: BASIC, watch: false });
    expect(m.snapshot()).toEqual({ state: "none" });
    expect(m.state().sampleAvailable).toBe(true);

    const bad = m.add("relative/path");
    expect(bad.ok).toBe(false);
    expect(m.add(join(d, "missing")).ok).toBe(false);

    const sample = m.addSample();
    expect(sample.ok && sample.source.label).toBe("Sample data");
    const snap = m.snapshot();
    expect(snap.state === "ok" && snap.snapshot.portfolios[0]?.strategies?.length).toBe(3);

    const empty = m.add(join(FIXTURES, "empty"));
    expect(empty.ok).toBe(true);
    const s2 = m.snapshot();
    expect(s2.state === "ok" && s2.snapshot.portfolios[0]?.strategies).toEqual([]);
    m.dispose();

    const again = new SourceManager({ storePath: store, samplePath: BASIC, watch: false });
    expect(again.state().sources.map((s) => s.label)).toEqual(["Sample data", "empty"]);
    expect(again.active()?.label).toBe("empty");
    again.setActive("sample");
    expect(again.active()?.label).toBe("Sample data");
    again.remove("sample");
    expect(again.active()?.label).toBe("empty");
    again.dispose();
  });

  it("picks up a changed CSV and reports a deleted one", () => {
    const d = tmp();
    const data = join(d, "cache");
    cpSync(BASIC, data, { recursive: true });
    const revisions: number[] = [];
    const m = new SourceManager({
      storePath: join(d, "sources.json"),
      samplePath: null,
      watch: false,
      onChange: (r) => revisions.push(r),
    });
    m.add(data);
    expect(m.checkForChanges()).toBe(false);
    const summary = join(data, "out/fixture/summary.csv");
    writeFileSync(summary, readFileSync(summary, "utf8").replace("1059.14", "1060.00"));
    expect(m.checkForChanges()).toBe(true);
    const snap = m.snapshot();
    expect(snap.state === "ok" && snap.snapshot.portfolios[0]?.summary?.equity_usd).toBe(1060);

    rmSync(join(data, "out/fixture/trades.csv"));
    const page = m.trades({ offset: 0, limit: 10 });
    expect(page.ok).toBe(false);
    expect(!page.ok && page.errors[0]?.kind).toBe("missing_file");
    expect(revisions.length).toBeGreaterThanOrEqual(3);
    m.dispose();
  });

  it("a corrupt store file starts empty", () => {
    const d = tmp();
    writeFileSync(join(d, "sources.json"), "{not json");
    const m = new SourceManager({ storePath: join(d, "sources.json"), samplePath: null, watch: false });
    expect(m.state()).toEqual({ sources: [], activeId: null, sampleAvailable: false });
    m.dispose();
  });
});
