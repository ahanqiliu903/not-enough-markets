// @vitest-environment node
import { describe, expect, it } from "vitest";
import { DB_SCHEMA_VERSIONS } from "@shared/db";
import { TRADE_COLUMNS, type StrategyRow, type TradeRow } from "@shared/data";
import { coerce, parseTable } from "./csv";
import { parseFilter } from "./handlers";
import type { LoadedSource } from "./source";
import { TradeIndex } from "./trades";

/** Contract 1.1 exclusion columns (`nem trade exclude`), synthetic rows only. */

const ROW = "1.0,p,s,W1,W1-00,yes,5,90.0,0.02,90.4,paper,2026-10-01T00:00:00Z,2026-10-01T00:15:00Z,,open,,,,,,,";
const HEADER = TRADE_COLUMNS.join(",");
const V10_HEADER = TRADE_COLUMNS.slice(0, 16).join(",");

function trades(text: string) {
  return parseTable<TradeRow>(text, "trades.csv", "trade_row.schema.json");
}

describe("excluded trades", () => {
  it("coerces the engine's true/false (and Sheets' TRUE/FALSE)", () => {
    expect(coerce("true", ["boolean", "null"])).toBe(true);
    expect(coerce("false", ["boolean", "null"])).toBe(false);
    expect(coerce("TRUE", ["boolean", "null"])).toBe(true);
    expect(coerce("", ["boolean", "null"])).toBeNull();
    expect(coerce("yes", ["boolean", "null"])).toBe("yes");
  });

  it("parses the excluded flag and reason", () => {
    const t = trades(`${HEADER}\n${ROW},true,"bad fill, stale price"\n${ROW.replace("W1,W1-00", "W2,W2-00")},false,\n`);
    expect(t.errors).toEqual([]);
    expect(t.rows?.map((r) => [r.excluded, r.exclude_reason])).toEqual([
      [true, "bad fill, stale price"],
      [false, null],
    ]);
  });

  it("an older file without the columns reads them as null", () => {
    const t = trades(`${V10_HEADER}\n${ROW.slice(0, ROW.length - 6)}\n`);
    expect(t.errors).toEqual([]);
    expect(t.rows?.[0]).toMatchObject({ excluded: null, exclude_reason: null });
  });

  it("an invalid flag is a schema error", () => {
    const t = trades(`${HEADER}\n${ROW},maybe,\n`);
    expect(t.rows).toBeNull();
    expect(t.errors.map((e) => e.field)).toContain("excluded");
  });

  it("strategies.csv without excluded_trades loads with null", () => {
    const header = (STRATEGY_HEADER.split(",").slice(0, -1)).join(",");
    const row = STRATEGY_ROW.split(",").slice(0, -1).join(",");
    const t = parseTable<StrategyRow>(`${header}\n${row}\n`, "strategies.csv", "strategy_row.schema.json");
    expect(t.errors).toEqual([]);
    expect(t.rows?.[0]?.excluded_trades).toBeNull();
    const full = parseTable<StrategyRow>(`${STRATEGY_HEADER}\n${STRATEGY_ROW}\n`, "strategies.csv", "strategy_row.schema.json");
    expect(full.rows?.[0]?.excluded_trades).toBe(2);
  });

  it("the trades query can hide excluded trades", () => {
    const text = `${HEADER}\n${ROW},true,dup\n${ROW.replace("W1,W1-00", "W2,W2-00")},false,\n`;
    const t = trades(text);
    const source = {
      portfolios: [{ name: "p", trades: t.rows, tradesRaw: t.raw, tradesHeader: t.header }],
    } as unknown as LoadedSource;
    const idx = new TradeIndex();
    expect(idx.page(source, 1, { offset: 0, limit: 10 }).total).toBe(2);
    const shown = idx.page(source, 1, { hideExcluded: true, offset: 0, limit: 10 });
    expect(shown.rows.map((r) => r.window_id)).toEqual(["W2"]);
    expect(parseFilter({ hideExcluded: true })).toEqual({ hideExcluded: true });
    expect(parseFilter({ hideExcluded: "yes" })).toEqual({});
  });

  it("the database reader accepts schema v8 and v9", () => {
    expect(DB_SCHEMA_VERSIONS).toContain(8);
    expect(DB_SCHEMA_VERSIONS).toContain(9);
  });
});

const STRATEGY_HEADER =
  "schema_version,portfolio,strategy,series,status,trades,wins,open,win_rate,win_rate_lo,win_rate_hi,break_even_rate,edge_cents,edge_lo_cents,edge_hi_cents,z,p,pnl_usd,pnl_per_trade_usd,roi,max_drawdown_usd,trades_to_detect,trades_to_detect_edge_cents,verdict,brier,open_value_usd,unrealized_pnl_usd,open_unmarked,open_no_bid_contracts,excluded_trades";
const STRATEGY_ROW = "1.0,p,s,KXFAKE,active,3,3,0,1.0,0.4,1.0,0.9,9.0,1.0,10.0,1.0,0.2,1.44,0.48,0.1,0.0,50,9.0,undecided,,0.0,0.0,0,0,2";
