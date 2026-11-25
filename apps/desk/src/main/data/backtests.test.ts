// @vitest-environment node
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { loadSource } from "./source";

/** DESK-015..017: backtest runs reach the renderer whole; DESK-014: status.json. */

const BASIC = resolve(import.meta.dirname, "../../../../../contract/fixtures/basic");
const RUN = "out/backtests/20261002T183500Z-fixture-sol_undecided-3d852795.json";
const tmps: string[] = [];

function copyBasic(): string {
  const d = mkdtempSync(join(tmpdir(), "nem-desk-bt-"));
  tmps.push(d);
  cpSync(BASIC, d, { recursive: true });
  rmSync(join(d, "nem.db"), { force: true });
  return d;
}

afterEach(() => {
  for (const d of tmps.splice(0)) rmSync(d, { recursive: true, force: true });
});

describe("backtest runs", () => {
  it("keeps the whole validated document: grid, windows, variants, chosen test only", () => {
    const src = loadSource(BASIC);
    const [run] = src.backtests;
    expect(src.backtests).toHaveLength(1);
    expect(run?.file).toBe(RUN);
    expect(run?.grid.map((g) => g.path)).toEqual(["signal.threshold", "gates.0.value"]);
    expect(run?.min_trades).toBe(20);
    expect(run?.chosen).toBe("v00");
    expect(run?.fill_estimate).toBeNull();
    expect(run?.train_window.end).toBe(run?.test_window.start);
    expect(run?.variants).toHaveLength(8);
    expect(run?.variants.filter((v) => v.test !== null).map((v) => v.name)).toEqual(["v00"]);
    expect(run?.variants[0]?.train.edge_cents).toBe(3.07);
    // contract 1.1 manifest: what `nem rerun` reproduces the run from
    expect(run?.dataset?.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(run?.fees?.find((f) => f.strategy === "sol_undecided")?.taker_fee_rate?.source).toBe("default");
  });

  it("an invalid run is a DataError naming the file; other runs still load", () => {
    const d = copyBasic();
    const good = JSON.parse(readFileSync(join(d, RUN), "utf8")) as Record<string, unknown>;
    writeFileSync(join(d, "out/backtests/bad.json"), JSON.stringify({ ...good, run_id: "bad", variants: "nope" }));
    writeFileSync(join(d, "out/backtests/v2.json"), JSON.stringify({ ...good, schema_version: "2.0" }));
    const src = loadSource(d);
    expect(src.backtests.map((b) => b.run_id)).toEqual([good["run_id"]]);
    expect(src.errors.some((e) => e.file === "out/backtests/bad.json" && e.kind === "invalid" && (e.field ?? "").includes("variants"))).toBe(true);
    expect(src.errors.find((e) => e.file === "out/backtests/v2.json")).toMatchObject({ kind: "version", found: "2.0" });
  });

  it("reads the fixture's status.json: processes, halts, strategy activity", () => {
    const st = loadSource(BASIC).status;
    expect(st?.processes.map((p) => [p.name, p.state])).toEqual([["runner", "ok"], ["reporter", "ok"]]);
    expect(st?.halts).toEqual([{ scope: "fixture/eth_no_edge", reason: "fixture: verdict no_edge", since: "2026-10-02T18:30:00Z" }]);
    expect(st?.strategies.find((s) => s.strategy === "eth_no_edge")?.halted_by).toBe("fixture/eth_no_edge");
  });
});
