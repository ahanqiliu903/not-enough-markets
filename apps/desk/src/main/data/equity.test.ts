// @vitest-environment node
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { fingerprint, loadSource } from "./source";
import { SourceManager } from "./sources";
import { equityCurve } from "./trades";

/** Contract 1.1 equity.csv (portfolio value over time): read leniently, never recomputed. */

const FIXTURES = resolve(import.meta.dirname, "../../../../../contract/fixtures");
const BASIC = join(FIXTURES, "basic");
const HEADER = "schema_version,portfolio,ts,book_value_usd,realized_pnl_usd,interest_usd,unrealized_pnl_usd,value_usd,open_positions,open_unmarked";
const tmps: string[] = [];

function copyBasic(): string {
  const d = mkdtempSync(join(tmpdir(), "nem-desk-equity-"));
  tmps.push(d);
  cpSync(BASIC, d, { recursive: true });
  return d;
}

afterEach(() => {
  for (const d of tmps.splice(0)) rmSync(d, { recursive: true, force: true });
});

describe("equity.csv", () => {
  it("the fixture loads: hourly points, the last one at the summary's as_of", () => {
    const src = loadSource(BASIC);
    const p = src.portfolios[0];
    expect(p?.errors).toEqual([]);
    const r = equityCurve(src, "fixture");
    if (!r.ok || r.curve === null) throw new Error("expected a curve");
    const c = r.curve;
    const lines = readFileSync(join(BASIC, "out/fixture/equity.csv"), "utf8").trim().split("\n");
    expect(c.t.length).toBe(lines.length - 1);
    expect((c.t[1] ?? 0) - (c.t[0] ?? 0)).toBe(3600);
    expect(c.t.at(-1)).toBe(Date.parse(p?.summary?.as_of ?? "") / 1000);
    expect(c.book.at(-1)).toBe(p?.summary?.equity_usd);
    expect(c.value[0]).toBe(1000);
  });

  it("absent (older engine): no curve and no error", () => {
    const d = copyBasic();
    rmSync(join(d, "out/fixture/equity.csv"));
    const src = loadSource(d);
    expect(src.portfolios[0]?.errors).toEqual([]);
    expect(equityCurve(src, "fixture")).toEqual({ ok: true, curve: null });
    expect(equityCurve(src, "nope")).toEqual({ ok: true, curve: null });
  });

  it("values are the engine's, in time order", () => {
    const d = copyBasic();
    writeFileSync(
      join(d, "out/fixture/equity.csv"),
      `${HEADER}\n1.1,fixture,2026-10-01T01:00:00Z,1000.5,0.5,0.0,-0.25,1000.25,1,0\n1.1,fixture,2026-10-01T00:00:00Z,1000.0,0.0,0.0,0.0,1000.0,0,0\n`,
    );
    const r = equityCurve(loadSource(d), "fixture");
    expect(r).toEqual({
      ok: true,
      curve: { portfolio: "fixture", t: [1790812800, 1790816400], value: [1000, 1000.25], book: [1000, 1000.5], lastUnmarked: 0 },
    });
  });

  it("an invalid file is a schema error for the chart only", () => {
    const d = copyBasic();
    writeFileSync(join(d, "out/fixture/equity.csv"), `${HEADER}\n1.0,fixture,2026-10-01 00:00,1000,0,0,0,1000,0,0\n`);
    const src = loadSource(d);
    expect(src.portfolios[0]?.summary).not.toBeNull();
    expect(src.portfolios[0]?.trades).not.toBeNull();
    const r = equityCurve(src, "fixture");
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors[0]).toMatchObject({ kind: "invalid", field: "ts" });
    const missing = copyBasic();
    writeFileSync(join(missing, "out/fixture/equity.csv"), `${HEADER.replace(",value_usd", "")}\n`);
    const m = equityCurve(loadSource(missing), "fixture");
    expect(!m.ok && m.errors[0]?.kind).toBe("missing_column");
  });

  it("a changed equity.csv changes the fingerprint; the manager serves it", () => {
    const d = copyBasic();
    const before = fingerprint(d);
    writeFileSync(join(d, "out/fixture/equity.csv"), `${HEADER}\n`);
    expect(fingerprint(d)).not.toBe(before);
    const m = new SourceManager({ storePath: join(d, "sources.json"), samplePath: null, watch: false });
    m.add(d);
    expect(m.equity("fixture")).toEqual({ ok: true, curve: { portfolio: "fixture", t: [], value: [], book: [], lastUnmarked: 0 } });
    expect(m.equity(undefined)).toEqual({ ok: true, curve: null });
    m.dispose();
  });
});
