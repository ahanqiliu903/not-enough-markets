// @vitest-environment node
import { describe, expect, it } from "vitest";
import { TRADE_COLUMNS, type SummaryRow, type TradeRow } from "@shared/data";
import { parseTable } from "./csv";

/** Contract 1.1 provisional columns, synthetic rows only. */

const ROW = "1.0,p,s,W1,W1-00,no,5,60.0,0.02,60.4,paper,2030-01-23T20:00:00Z,2030-01-24T07:59:00Z,,open,,,,,,,,false,";
const HEADER = TRADE_COLUMNS.join(",");

function trades(text: string) {
  return parseTable<TradeRow>(text, "trades.csv", "trade_row.schema.json");
}

describe("provisional columns", () => {
  it("parses a trade's provisional result", () => {
    const t = trades(`${HEADER}\n${ROW},locked_win,observed,1.98,56\n`);
    expect(t.errors).toEqual([]);
    expect(t.rows?.[0]).toMatchObject({
      provisional_result: "locked_win",
      provisional_basis: "observed",
      provisional_pnl_usd: 1.98,
      observed_extreme_f: 56,
    });
  });

  it("an older file without the columns reads them as null", () => {
    const header = TRADE_COLUMNS.slice(0, -4).join(",");
    const t = trades(`${header}\n${ROW}\n`);
    expect(t.errors).toEqual([]);
    expect(t.rows?.[0]).toMatchObject({ provisional_result: null, provisional_basis: null, provisional_pnl_usd: null, observed_extreme_f: null });
  });

  it("an unknown result value is kept, not rejected", () => {
    const t = trades(`${HEADER}\n${ROW},settled_soon,,,\n`);
    expect(t.errors).toEqual([]);
    expect(t.rows?.[0]?.provisional_result).toBe("settled_soon");
  });

  it("a non-integer extreme is a schema error", () => {
    const t = trades(`${HEADER}\n${ROW},locked_win,observed,1.98,hot\n`);
    expect(t.rows).toBeNull();
    expect(t.errors.map((e) => e.field)).toContain("observed_extreme_f");
  });

  it("summary pending columns: present, and absent in an older file", () => {
    const cols = [
      "schema_version", "portfolio", "mode", "as_of", "started_at", "starting_balance_usd", "realized_pnl_usd",
      "interest_usd", "equity_usd", "return_rate", "open_positions", "open_cost_usd", "interest_apy",
      "cash_benchmark_usd", "pnl_vs_cash_usd", "open_value_usd", "unrealized_pnl_usd", "open_unmarked",
      "open_no_bid_contracts", "excluded_trades",
    ];
    const row = "1.0,p,paper,2026-11-24T00:00:00Z,,1000,0,0,1000,0,2,9.04,,,,,,2,,0";
    const pending = ["pending_locked_pnl_usd", "pending_likely_pnl_usd", "pending_locked", "pending_likely", "pending_conflict"];
    const full = parseTable<SummaryRow>(`${[...cols, ...pending].join(",")}\n${row},1.98,-4.52,1,1,0\n`, "summary.csv", "summary_row.schema.json");
    expect(full.errors).toEqual([]);
    expect(full.rows?.[0]).toMatchObject({ pending_locked_pnl_usd: 1.98, pending_likely_pnl_usd: -4.52, pending_locked: 1, pending_likely: 1, pending_conflict: 0 });
    const old = parseTable<SummaryRow>(`${cols.join(",")}\n${row}\n`, "summary.csv", "summary_row.schema.json");
    expect(old.errors).toEqual([]);
    expect(old.rows?.[0]).toMatchObject({ pending_locked_pnl_usd: null, pending_locked: null, pending_conflict: null });
  });
});
