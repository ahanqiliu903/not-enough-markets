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
  kind: "folder" | "sample" | "sqlite" | "csv" | "json";
  /** Absolute path the user picked (an `out/` folder or its parent). */
  path: string;
}

export interface SourcesState {
  sources: DataSource[];
  activeId: string | null;
  /** True when the bundled sample data folder exists (dev: contract/fixtures/basic). */
  sampleAvailable: boolean;
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
  backtests: BacktestRunInfo[];
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
