import type {
  DataError,
  Snapshot,
  SnapshotResult,
  SourcesState,
  StrategyRow,
  SummaryRow,
  TradeRow,
  TradesPage,
  TradesQuery,
} from "@shared/data";
import type { NemApi } from "@shared/ipc";

/**
 * Renderer test data: values copied from contract/fixtures/basic (the main-process tests
 * read the fixture files themselves) and a fake `window.nem`.
 */

const base = { schema_version: "1.0", portfolio: "fixture" } as const;

export const SUMMARY: SummaryRow = {
  ...base,
  mode: "paper",
  as_of: "2026-10-02T18:35:00Z",
  started_at: "2026-10-01T00:00:00Z",
  starting_balance_usd: 1000,
  realized_pnl_usd: 58.92,
  interest_usd: 0.22,
  equity_usd: 1059.14,
  return_rate: 0.0591,
  open_positions: 0,
  open_cost_usd: 0,
  interest_apy: 0.04,
  cash_benchmark_usd: 0.19,
  pnl_vs_cash_usd: 58.95,
  open_value_usd: 0,
  unrealized_pnl_usd: 0,
  open_unmarked: 0,
  open_no_bid_contracts: 0,
  excluded_trades: 0,
};

function strat(o: Partial<StrategyRow> & Pick<StrategyRow, "strategy" | "series" | "verdict">): StrategyRow {
  return {
    ...base,
    status: "active",
    trades: 0,
    wins: 0,
    open: 0,
    win_rate: null,
    win_rate_lo: null,
    win_rate_hi: null,
    break_even_rate: null,
    edge_cents: null,
    edge_lo_cents: null,
    edge_hi_cents: null,
    z: null,
    p: null,
    pnl_usd: 0,
    pnl_per_trade_usd: null,
    roi: null,
    max_drawdown_usd: 0,
    trades_to_detect: null,
    trades_to_detect_edge_cents: 2,
    brier: null,
    open_value_usd: 0,
    unrealized_pnl_usd: 0,
    open_unmarked: 0,
    open_no_bid_contracts: 0,
    excluded_trades: 0,
    ...o,
  };
}

export const STRATEGIES: StrategyRow[] = [
  strat({ strategy: "btc_edge", series: "KXBTC15M", verdict: "edge", trades: 170, wins: 169, win_rate: 0.9941, win_rate_lo: 0.9674, win_rate_hi: 0.999, break_even_rate: 0.8945, edge_cents: 9.96, edge_lo_cents: 7.29, edge_hi_cents: 10.45, z: 4.229, p: 0, pnl_usd: 84.67, pnl_per_trade_usd: 0.4981, roi: 0.1114, max_drawdown_usd: 4.54, trades_to_detect: 33, trades_to_detect_edge_cents: 9.96, brier: 0.0081 }),
  strat({ strategy: "eth_no_edge", series: "KXETH15M", verdict: "no_edge", trades: 54, wins: 39, win_rate: 0.7222, win_rate_lo: 0.5911, win_rate_hi: 0.8238, break_even_rate: 0.8919, edge_cents: -16.96, edge_lo_cents: -30.08, edge_hi_cents: -6.8, z: -4.015, p: 1, pnl_usd: -45.8, pnl_per_trade_usd: -0.8481, roi: -0.1902, max_drawdown_usd: 46.31, trades_to_detect: 1405, brier: 0.2367 }),
  strat({ strategy: "sol_undecided", series: "KXSOL15M", verdict: "undecided", trades: 170, wins: 156, win_rate: 0.9176, win_rate_lo: 0.8665, win_rate_hi: 0.9503, break_even_rate: 0.8941, edge_cents: 2.36, edge_lo_cents: -2.75, edge_hi_cents: 5.63, z: 1, p: 0.1587, pnl_usd: 20.05, pnl_per_trade_usd: 0.1179, roi: 0.0264, max_drawdown_usd: 12.66, trades_to_detect: 978, trades_to_detect_edge_cents: 2.36, brier: 0.076 }),
];

export function trade(i: number, strategy = "btc_edge"): TradeRow {
  const mm = String(i % 60).padStart(2, "0");
  return {
    ...base,
    strategy,
    window_id: `KXBTC15M-W${i}`,
    ticker: `KXBTC15M-W${i}-00`,
    side: "yes",
    contracts: 5,
    entry_price_cents: 89,
    fee_usd: 0.04,
    cost_per_contract_cents: 89.8,
    mode: "paper",
    opened_at: `2026-10-01T00:${mm}:00Z`,
    close_time: `2026-10-01T00:${mm}:30Z`,
    settled_at: `2026-10-01T00:${mm}:33Z`,
    result: i % 10 === 0 ? "lost" : "won",
    pnl_usd: i % 10 === 0 ? -4.49 : 0.51,
    mark_bid_cents: null,
    mark_mid_cents: null,
    mark_contracts: null,
    mark_value_usd: null,
    unrealized_pnl_usd: null,
    marked_at: null,
    excluded: false,
    exclude_reason: null,
  };
}

export const SOURCE = { id: "sample", label: "Sample data", kind: "sample", path: "/fixtures/basic" } as const;

export function snapshot(over: Partial<Snapshot> = {}): Snapshot {
  return {
    source: SOURCE,
    outDir: "/fixtures/basic/out",
    loadedAt: "2026-10-02T18:40:00Z",
    revision: 1,
    portfolios: [{ name: "fixture", summary: SUMMARY, strategies: STRATEGIES, tradeCount: 394, errors: [] }],
    extras: {
      "fixture/btc_edge": {
        signal_reasons: { "ok: fixed 5": 170 },
        calibration: [
          { lo_rate: 0.92, hi_rate: 0.94, n: 34, predicted_rate: 0.9351, observed_rate: 1 },
          { lo_rate: 0.94, hi_rate: 0.96, n: 126, predicted_rate: 0.9508, observed_rate: 0.9921 },
        ],
      },
    },
    hasSummaryJson: true,
    status: null,
    backtests: [],
    errors: [],
    newestAsOf: "2026-10-02T18:35:00Z",
    ...over,
  };
}

export const MISSING_COLUMN: DataError = {
  kind: "missing_column",
  file: "out/fixture/strategies.csv",
  field: "edge_cents",
  message: "missing columns ['edge_cents']",
  expected: "1.x",
};

export interface FakeOptions {
  snapshot?: SnapshotResult;
  totalTrades?: number;
  tradesError?: DataError[];
  /** Row generator for the trades pages (default `trade`). */
  tradeRow?: (i: number) => TradeRow;
}

export interface Fake {
  api: NemApi;
  calls: { trades: TradesQuery[]; addSample: number };
}

/** A fake preload API backed by in-memory data; trades are generated per page. */
export function fakeApi(o: FakeOptions = {}): Fake {
  const calls = { trades: [] as TradesQuery[], addSample: 0 };
  const total = o.totalTrades ?? 50;
  const state: SourcesState = { sources: [SOURCE], activeId: SOURCE.id, sampleAvailable: true };
  const snap: SnapshotResult = o.snapshot ?? { state: "ok", snapshot: snapshot() };
  const api: NemApi = {
    appVersion: () => Promise.resolve("test"),
    listSources: () => Promise.resolve([SOURCE]),
    data: {
      snapshot: () => Promise.resolve(snap),
      trades: (q) => {
        calls.trades.push(q);
        if (o.tradesError !== undefined) return Promise.resolve<TradesPage>({ ok: false, errors: o.tradesError });
        const n = Math.max(0, Math.min(q.limit, total - q.offset));
        const make = o.tradeRow ?? trade;
        const rows = Array.from({ length: n }, (_, i) => make(q.offset + i));
        return Promise.resolve<TradesPage>({ ok: true, revision: 1, total, offset: q.offset, rows });
      },
      tradesCsv: () => Promise.resolve({ ok: true, csv: "", rows: 0 }),
      curves: (keys) =>
        Promise.resolve({
          ok: true,
          curves: keys.map((k) => ({ ...k, t: [1759277703, 1759278603], pnl: [0.51, 1.07] })),
        }),
      onChanged: () => () => undefined,
      db: undefined as unknown as NemApi["data"]["db"],
      updates: undefined as unknown as NemApi["data"]["updates"],
      diagnostics: undefined as unknown as NemApi["data"]["diagnostics"],
    },
    sources: {
      list: () => Promise.resolve(snap.state === "none" ? { ...state, sources: [], activeId: null } : state),
      addFolder: () => Promise.resolve({ ok: false, state, message: "cancelled" }),
      addSample: () => {
        calls.addSample++;
        return Promise.resolve({ ok: true, state, source: SOURCE });
      },
      remove: () => Promise.resolve(state),
      setActive: () => Promise.resolve(state),
      attachDb: () => Promise.resolve({ ok: false, state, message: "cancelled" }),
      detachDb: () => Promise.resolve(state),
      vps: undefined as unknown as NemApi["sources"]["vps"],
      sheets: undefined as unknown as NemApi["sources"]["sheets"],
    },
    kalshi: undefined as unknown as NemApi["kalshi"],
    platform: "linux",
  };
  return { api, calls };
}
