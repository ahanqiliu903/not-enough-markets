// @vitest-environment node
import { describe, expect, it } from "vitest";
import { TRADE_COLUMNS, type SummaryRow, type TradeRow } from "@shared/data";
import { parseTable } from "./csv";

/** Contract 1.1 mark-to-market columns (`x-since`), synthetic rows only. */

const V10_TRADE_HEADER = TRADE_COLUMNS.slice(0, 16).join(",");
const V10_TRADE =
  "1.0,p,s,W1,W1-00,yes,5,90.0,0.02,90.4,paper,2026-10-01T00:00:00Z,2026-10-01T00:15:00Z,,open,";

describe("mark columns", () => {
  it("a 1.0 trades.csv without them loads, marks read as null", () => {
    const t = parseTable<TradeRow>(`${V10_TRADE_HEADER}\n${V10_TRADE}\n`, "trades.csv", "trade_row.schema.json");
    expect(t.errors).toEqual([]);
    expect(t.rows?.[0]).toMatchObject({ result: "open", mark_value_usd: null, unrealized_pnl_usd: null, marked_at: null });
  });

  it("rows may stop before trailing x-since cells; any other short row is an error", () => {
    const text = `${TRADE_COLUMNS.join(",")}\n${V10_TRADE}\n${V10_TRADE.replace(/,open,$/, ",open")}\n`;
    const t = parseTable<TradeRow>(text, "trades.csv", "trade_row.schema.json");
    expect(t.rows).toBeNull();
    expect(t.errors).toEqual([expect.objectContaining({ kind: "parse", row: 2 })]);
    const ok = parseTable<TradeRow>(`${TRADE_COLUMNS.join(",")}\n${V10_TRADE}\n`, "trades.csv", "trade_row.schema.json");
    expect(ok.rows?.[0]).toMatchObject({ pnl_usd: null, mark_contracts: null, marked_at: null });
  });

  it("parses engine marks on an open trade", () => {
    const text = `${TRADE_COLUMNS.join(",")}\n${V10_TRADE},88.0,89.0,5,4.38,-0.14,2026-10-01T00:05:00Z\n`;
    const t = parseTable<TradeRow>(text, "trades.csv", "trade_row.schema.json");
    expect(t.errors).toEqual([]);
    expect(t.rows?.[0]).toMatchObject({
      mark_bid_cents: 88,
      mark_mid_cents: 89,
      mark_contracts: 5,
      mark_value_usd: 4.38,
      unrealized_pnl_usd: -0.14,
      marked_at: "2026-10-01T00:05:00Z",
    });
  });

  it("an invalid mark value is still a schema error", () => {
    const text = `${TRADE_COLUMNS.join(",")}\n${V10_TRADE},88.0,89.0,2.5,4.38,,2026-10-01 00:05\n`;
    const t = parseTable<TradeRow>(text, "trades.csv", "trade_row.schema.json");
    expect(t.rows).toBeNull();
    expect(t.errors.map((e) => e.field)).toEqual(expect.arrayContaining(["mark_contracts", "marked_at"]));
  });

  it("a 1.0 summary.csv loads with null open value; other missing columns still block", () => {
    const header =
      "schema_version,portfolio,mode,as_of,started_at,starting_balance_usd,realized_pnl_usd,interest_usd,equity_usd,return_rate,open_positions,open_cost_usd,interest_apy,cash_benchmark_usd,pnl_vs_cash_usd";
    const row = "1.0,p,paper,2026-10-01T00:00:00Z,,1000,0,0,1000,0,0,0,,,";
    const ok = parseTable<SummaryRow>(`${header}\n${row}\n`, "summary.csv", "summary_row.schema.json");
    expect(ok.errors).toEqual([]);
    expect(ok.rows?.[0]).toMatchObject({ open_value_usd: null, unrealized_pnl_usd: null, open_unmarked: null, open_no_bid_contracts: null });
    const cut = header.replace(",open_cost_usd", "");
    const bad = parseTable<SummaryRow>(`${cut}\n${row.replace(/,0,0,,,$/, ",0,,,")}\n`, "summary.csv", "summary_row.schema.json");
    expect(bad.errors[0]).toMatchObject({ kind: "missing_column", field: "open_cost_usd" });
  });
});
