/**
 * Data-layer types shared by main and renderer (DESK-003/004). Row shapes mirror
 * `contract/schema/*.json` (contract 1.x). Values are the engine's, coerced from CSV text
 * by the rules in `tests/contract/test_contract.py`; nothing here is recomputed.
 */

export type Verdict = "edge" | "no_edge" | "undecided";
export type StrategyStatus = "active" | "paused" | "retired";
export type Mode = "paper" | "live";
export type TradeResult = "won" | "lost" | "open";

/** The contract major this build reads. Any other major is a blocking error. */
export const CONTRACT_MAJOR = 1;
export const EXPECTED_VERSION = `${CONTRACT_MAJOR}.x`;

export interface SummaryRow {
  schema_version: string;
  portfolio: string;
  mode: Mode;
  as_of: string;
  started_at: string | null;
  starting_balance_usd: number;
  realized_pnl_usd: number;
  interest_usd: number;
  equity_usd: number;
  return_rate: number;
  open_positions: number;
  open_cost_usd: number;
  interest_apy: number | null;
  cash_benchmark_usd: number | null;
  pnl_vs_cash_usd: number | null;
  /**
   * Contract 1.1 (null when an older 1.x file lacks them): open positions at liquidation
   * value (recorded bids, after the exit fee; contracts without bids at $0), summed over
   * open trades with a fresh book; `open_unmarked` counts the open trades left out (stale,
   * closed); `open_no_bid_contracts` the contracts valued at $0 because nobody bids.
   * Engine values, never recomputed.
   */
  open_value_usd: number | null;
  unrealized_pnl_usd: number | null;
  open_unmarked: number | null;
  open_no_bid_contracts: number | null;
  /**
   * Contract 1.1 (null when an older 1.x file lacks it): excluded trades
   * (`nem trade exclude`), settled and open. Treated as never taken: left out of every
   * statistic and money total the engine reports.
   */
  excluded_trades: number | null;
  /**
   * Contract 1.1 (null when not computed or an older 1.x file lacks them): provisional
   * results of counted open trades (`nem.settle.provisional`): P&L if the locked / likely
   * ones settle that way, their counts, and conflicts (observed vs price, not summed).
   * Reporting only: not counted in P&L, equity or statistics; Kalshi settles.
   */
  pending_locked_pnl_usd: number | null;
  pending_likely_pnl_usd: number | null;
  pending_locked: number | null;
  pending_likely: number | null;
  pending_conflict: number | null;
}

export interface StrategyRow {
  schema_version: string;
  portfolio: string;
  strategy: string;
  series: string;
  status: StrategyStatus;
  trades: number;
  wins: number;
  open: number;
  win_rate: number | null;
  win_rate_lo: number | null;
  win_rate_hi: number | null;
  break_even_rate: number | null;
  edge_cents: number | null;
  edge_lo_cents: number | null;
  edge_hi_cents: number | null;
  z: number | null;
  p: number | null;
  pnl_usd: number;
  pnl_per_trade_usd: number | null;
  roi: number | null;
  max_drawdown_usd: number;
  trades_to_detect: number | null;
  trades_to_detect_edge_cents: number;
  verdict: Verdict;
  brier: number | null;
  /**
   * Contract 1.1 (null when an older 1.x file lacks them): open positions at liquidation
   * value (recorded bids, after the exit fee; contracts without bids at $0), summed over
   * open trades with a fresh book; `open_unmarked` counts the open trades left out (stale,
   * closed); `open_no_bid_contracts` the contracts valued at $0 because nobody bids.
   * Engine values, never recomputed.
   */
  open_value_usd: number | null;
  unrealized_pnl_usd: number | null;
  open_unmarked: number | null;
  open_no_bid_contracts: number | null;
  /**
   * Contract 1.1 (null when an older 1.x file lacks it): excluded trades
   * (`nem trade exclude`), settled and open. Treated as never taken: left out of every
   * statistic and money total the engine reports.
   */
  excluded_trades: number | null;
  /**
   * Contract 1.1 (null when not computed or an older 1.x file lacks them): provisional
   * results of counted open trades (`nem.settle.provisional`): P&L if the locked / likely
   * ones settle that way, their counts, and conflicts (observed vs price, not summed).
   * Reporting only: not counted in P&L, equity or statistics; Kalshi settles.
   */
  pending_locked_pnl_usd: number | null;
  pending_likely_pnl_usd: number | null;
  pending_locked: number | null;
  pending_likely: number | null;
  pending_conflict: number | null;
}

export interface TradeRow {
  schema_version: string;
  portfolio: string;
  strategy: string;
  window_id: string;
  ticker: string;
  side: "yes" | "no";
  contracts: number;
  entry_price_cents: number;
  fee_usd: number;
  cost_per_contract_cents: number;
  mode: Mode;
  opened_at: string;
  close_time: string;
  settled_at: string | null;
  result: TradeResult;
  pnl_usd: number | null;
  /**
   * Contract 1.1 mark-to-market of an open trade (null when settled, stale, closed, or an
   * older file): average sale price over the contracts the recorded bids cover, the mid
   * (reference only), covered contracts, proceeds after the exit fee (contracts beyond
   * `mark_contracts` have no bids and count at $0), and unrealized P&L.
   */
  mark_bid_cents: number | null;
  mark_mid_cents: number | null;
  mark_contracts: number | null;
  mark_value_usd: number | null;
  unrealized_pnl_usd: number | null;
  marked_at: string | null;
  /**
   * Contract 1.1: excluded (`nem trade exclude`: treated as never taken) and why. Null
   * (= counted) when an older 1.x file lacks the columns.
   */
  excluded: boolean | null;
  exclude_reason: string | null;
  /**
   * Contract 1.1 provisional result of an open trade (null when settled, not computed, or
   * an older file): `locked_win`, `locked_loss`, `likely_win`, `likely_loss`, `conflict`,
   * `unknown` (shown as text if unrecognised); basis `observed` / `price` / `both`; P&L if
   * it holds; the climate day's running high/low (°F) for temperature series. Not counted.
   */
  provisional_result: string | null;
  provisional_basis: string | null;
  provisional_pnl_usd: number | null;
  observed_extreme_f: number | null;
}

export const TRADE_COLUMNS = [
  "schema_version",
  "portfolio",
  "strategy",
  "window_id",
  "ticker",
  "side",
  "contracts",
  "entry_price_cents",
  "fee_usd",
  "cost_per_contract_cents",
  "mode",
  "opened_at",
  "close_time",
  "settled_at",
  "result",
  "pnl_usd",
  // contract 1.1: mark-to-market of open trades
  "mark_bid_cents",
  "mark_mid_cents",
  "mark_contracts",
  "mark_value_usd",
  "unrealized_pnl_usd",
  "marked_at",
  // contract 1.1: excluded from statistics
  "excluded",
  "exclude_reason",
  // contract 1.1: provisional result of open trades (not counted)
  "provisional_result",
  "provisional_basis",
  "provisional_pnl_usd",
  "observed_extreme_f",
] as const satisfies readonly (keyof TradeRow)[];

export type TradeColumn = (typeof TRADE_COLUMNS)[number];

export interface CalibrationBin {
  lo_rate: number;
  hi_rate: number;
  n: number;
  predicted_rate: number | null;
  observed_rate: number | null;
}

/** Per-strategy extras that only `summary.json` (`nem summary --json`) carries. */
export interface StrategyExtras {
  signal_reasons: Record<string, number>;
  calibration: CalibrationBin[];
}

export interface StatusReport {
  schema_version: string;
  as_of: string;
  healthy: boolean;
  processes: {
    name: string;
    state: "ok" | "failing" | "stale" | "never";
    last_heartbeat_at: string | null;
    detail: string | null;
    expected: boolean;
  }[];
  halts: { scope: string; reason: string; since: string }[];
  strategies: {
    portfolio: string;
    strategy: string;
    series: string;
    status: StrategyStatus;
    halted_by: string | null;
    open: number;
    settled: number;
    last_signal_at: string | null;
  }[];
}

/** Only the fields the desk reads today; the full document is validated in main. */
export interface BacktestRunInfo {
  file: string;
  run_id: string;
  created_at: string;
  portfolio: string;
  strategy: string;
  series: string;
  status: "suggested" | "rejected" | "insufficient";
}

/** `variant_metrics.schema.json`: one variant's metrics on one window (engine values). */
export interface VariantMetrics {
  trades: number;
  wins: number;
  win_rate: number | null;
  win_rate_lo: number | null;
  win_rate_hi: number | null;
  break_even_rate: number | null;
  edge_cents: number | null;
  edge_lo_cents: number | null;
  edge_hi_cents: number | null;
  z: number | null;
  p: number | null;
  pnl_usd: number;
  pnl_per_trade_usd: number | null;
  roi: number | null;
  max_drawdown_usd: number;
  trades_to_detect: number | null;
  trades_to_detect_edge_cents: number;
  verdict: Verdict;
  brier: number | null;
}

export interface BacktestVariant {
  name: string;
  /** Grid path -> value for this variant. */
  overrides: Record<string, unknown>;
  /** Position of each value in `grid[k].values`, one entry per grid axis. */
  index: number[];
  train: VariantMetrics;
  /** Held-out metrics; present for the chosen variant only, by design. */
  test: VariantMetrics | null;
}

export interface BacktestWindow {
  start: string;
  end: string;
}

/** A full `out/backtests/<run_id>.json` (`backtest_run.schema.json`), validated in main. */
export interface BacktestRun extends BacktestRunInfo {
  schema_version: string;
  min_trades: number;
  grid: { path: string; values: unknown[] }[];
  train_window: BacktestWindow;
  test_window: BacktestWindow;
  chosen: string | null;
  score_cents: number | null;
  reasons: string[];
  variants: BacktestVariant[];
  /** Always null in contract 1.0 (no fill range is modelled yet). */
  fill_estimate: Record<string, unknown> | null;
}

export type DataErrorKind =
  | "missing_file"
  | "unreadable"
  | "parse"
  | "missing_column"
  | "version"
  | "invalid"
  | "duplicate"
  | "source";

/**
 * A structured problem with the source. Never thrown to the renderer: screens show these
 * with `<SchemaError>`. `file` is relative to the source folder.
 */
export interface DataError {
  kind: DataErrorKind;
  file: string;
  /** 1-based data row (header excluded), when the problem is in one row. */
  row?: number;
  field?: string;
  message: string;
  expected?: string;
  found?: string;
}

export interface PortfolioData {
  name: string;
  /** Null when summary.csv is missing or failed validation (see `errors`). */
  summary: SummaryRow | null;
  /** Null when strategies.csv is missing or failed validation. */
  strategies: StrategyRow[] | null;
  /** Null when trades.csv is missing or failed validation. */
  tradeCount: number | null;
  errors: DataError[];
}

export interface DataSource {
  id: string;
  label: string;
  kind: "folder" | "sample" | "sqlite" | "csv" | "json" | "vps" | "sheets";
  /** Absolute path the user picked (an `out/` folder or its parent). */
  path: string;
  /**
   * DESK-010: a snapshot database the user attached to this source. Without one, main looks
   * for `nem.db` or `data/snapshot/nem.db` inside `path` (see `SourcesState.dbPaths`).
   */
  dbPath?: string;
}

export interface SourcesState {
  sources: DataSource[];
  activeId: string | null;
  /** True when the bundled sample data folder exists (dev: contract/fixtures/basic). */
  sampleAvailable: boolean;
  /** DESK-010: each source's database path (attached or found), null when it has none. */
  dbPaths?: Record<string, string | null>;
}

export type SourceResult =
  | { ok: true; state: SourcesState; source: DataSource }
  | { ok: false; state: SourcesState; message: string };

export interface Snapshot {
  source: DataSource;
  /** Folder holding the portfolio folders. */
  outDir: string;
  loadedAt: string;
  /** Bumped whenever any file in the source changes. */
  revision: number;
  portfolios: PortfolioData[];
  /** `summary.json` extras keyed `portfolio/strategy`; empty when the file is absent. */
  extras: Record<string, StrategyExtras>;
  hasSummaryJson: boolean;
  status: StatusReport | null;
  /** Every valid backtest run, full document (small: one sweep each), oldest file first. */
  backtests: BacktestRun[];
  /** Problems with source-level files (summary.json, status.json, backtests, folder). */
  errors: DataError[];
  /** Newest `as_of` across summary.csv rows and status.json, for the stale banner. */
  newestAsOf: string | null;
}

export type SnapshotResult =
  | { state: "none" }
  | { state: "error"; source: DataSource; errors: DataError[] }
  | { state: "ok"; snapshot: Snapshot };

export type SortDir = "asc" | "desc";

export interface TradesFilter {
  portfolio?: string;
  strategy?: string;
  result?: TradeResult;
  /** Case-insensitive substring of ticker, window_id or strategy. */
  text?: string;
  /** Leave out trades the engine excluded from statistics. */
  hideExcluded?: boolean;
}

export interface TradesQuery extends TradesFilter {
  sort?: { column: TradeColumn; dir: SortDir };
  offset: number;
  limit: number;
}

export type TradesPage =
  | {
      ok: true;
      revision: number;
      /** Rows matching the filter. */
      total: number;
      offset: number;
      rows: TradeRow[];
    }
  | { ok: false; errors: DataError[] };

export type TradesCsv = { ok: true; csv: string; rows: number } | { ok: false; errors: DataError[] };

export interface CurveKey {
  portfolio: string;
  strategy: string;
}

/** Cumulative realized P&L over settled trades, in settlement order (a chart helper). */
export interface PnlCurve extends CurveKey {
  /** Unix seconds of each settlement. */
  t: number[];
  /** Cumulative pnl_usd after each settled trade. */
  pnl: number[];
}

export type CurvesResult = { ok: true; curves: PnlCurve[] } | { ok: false; errors: DataError[] };

export function curveId(k: CurveKey): string {
  return `${k.portfolio}/${k.strategy}`;
}
