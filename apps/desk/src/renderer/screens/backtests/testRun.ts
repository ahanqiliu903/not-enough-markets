import type { BacktestRun, VariantMetrics } from "@shared/data";

/**
 * Renderer test data: the `basic` fixture's backtest run (contract/fixtures/basic/out/
 * backtests/), as a contract 1.0 run (no manifest), trimmed to the fields that differ per
 * variant. The main-process tests read the file itself.
 */

function m(o: Partial<VariantMetrics> & Pick<VariantMetrics, "trades" | "wins" | "edge_cents">): VariantMetrics {
  return {
    win_rate: null,
    win_rate_lo: null,
    win_rate_hi: null,
    break_even_rate: null,
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
    verdict: "undecided",
    brier: null,
    ...o,
  };
}

const TRAIN_V00 = m({ trades: 119, wins: 108, win_rate: 0.9076, win_rate_lo: 0.842, win_rate_hi: 0.9476, break_even_rate: 0.8769, edge_cents: 3.07, edge_lo_cents: -3.48, edge_hi_cents: 7.07, z: 1.02, p: 0.1539, pnl_usd: 18.27, trades_to_detect: 653, trades_to_detect_edge_cents: 3.07 });
export const TEST_V00 = m({ trades: 51, wins: 48, win_rate: 0.9412, win_rate_lo: 0.8408, win_rate_hi: 0.9798, break_even_rate: 0.8765, edge_cents: 6.46, edge_lo_cents: -3.58, edge_hi_cents: 10.32, z: 1.403, p: 0.0802, pnl_usd: 16.48, trades_to_detect: 131, trades_to_detect_edge_cents: 6.46 });

// threshold index, train trades, wins, edge cents (both gate values give the same train)
const ROWS: [number, number, number, number][] = [
  [0, 119, 108, 3.07],
  [1, 119, 108, 1.33],
  [2, 96, 86, -1.67],
  [3, 61, 56, -1.1],
];

const THRESHOLDS = [0.86, 0.88, 0.9, 0.92];
const GATES = [0.95, 0.97];

export const RUN: BacktestRun = {
  file: "out/backtests/20261002T183500Z-fixture-sol_undecided.json",
  schema_version: "1.0",
  run_id: "20261002T183500Z-fixture-sol_undecided",
  created_at: "2026-10-02T18:35:00Z",
  portfolio: "fixture",
  strategy: "sol_undecided",
  series: "KXSOL15M",
  status: "suggested",
  min_trades: 20,
  grid: [
    { path: "signal.threshold", values: THRESHOLDS },
    { path: "gates.0.value", values: GATES },
  ],
  train_window: { start: "2026-10-01T00:00:00Z", end: "2026-10-02T05:45:00Z" },
  test_window: { start: "2026-10-02T05:45:00Z", end: "2026-10-02T18:29:00.000001Z" },
  chosen: "v00",
  score_cents: 1.35,
  reasons: ["Chose v00 (signal.threshold=0.86, gates.0.value=0.95) on train only.", "This is a starting point for paper trading, not evidence of an edge."],
  variants: ROWS.flatMap(([i, trades, wins, edge]) =>
    GATES.map((g, j) => {
      const n = i * 2 + j;
      return {
        name: `v0${n}`,
        overrides: { "signal.threshold": THRESHOLDS[i], "gates.0.value": g },
        index: [i, j],
        train: n === 0 ? TRAIN_V00 : m({ trades, wins, edge_cents: edge }),
        test: n === 0 ? TEST_V00 : null,
      };
    }),
  ),
  fill_estimate: null,
};
