/**
 * Types for the read-only Kalshi explorer (DESK-024). The client lives in the main process
 * (`src/main/kalshi/`); the renderer only sees these shapes over the `kalshi:*` channels.
 * Prices are dollars (0.932 = 93.2c), sizes are contracts (fractional), times are ISO 8601.
 */

export const KALSHI_HOST = "api.elections.kalshi.com";
export const KALSHI_BASE_URL = `https://${KALSHI_HOST}/trade-api/v2`;

/** Series, event and market tickers: letters, digits, '.', '_', '-'. */
export const TICKER_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

export function isTicker(value: unknown): value is string {
  return typeof value === "string" && TICKER_RE.test(value);
}

export const STRIKE_TYPES = ["greater", "greater_or_equal", "less", "less_or_equal", "between"] as const;
export type StrikeType = (typeof STRIKE_TYPES)[number];

export interface KalshiMarket {
  ticker: string;
  eventTicker: string;
  title: string;
  yesSubTitle: string;
  noSubTitle: string;
  status: string;
  openTime: string;
  closeTime: string;
  /** null = no bid (wire value 0). */
  yesBid: number | null;
  /** null = no ask (wire value 1). */
  yesAsk: number | null;
  noBid: number | null;
  noAsk: number | null;
  lastPrice: number | null;
  result: "yes" | "no" | null;
  settledAt: string | null;
  strikeType: StrikeType | null;
  floorStrike: number | null;
  capStrike: number | null;
  /** `strike_type: "custom"` (e.g. multi-coin races): floor/cap don't describe YES. */
  customStrike: boolean;
  canCloseEarly: boolean;
  rulesPrimary: string | null;
  rulesSecondary: string | null;
  volume: number | null;
  openInterest: number | null;
}

export interface SettlementSource {
  name: string;
  url: string | null;
}

export interface KalshiSeries {
  ticker: string;
  title: string;
  category: string;
  frequency: string;
  settlementSources: SettlementSource[];
  contractTermsUrl: string | null;
  /** Kalshi's "important information" note (markdown), when present. */
  importantInfo: string | null;
  tags: string[];
}

export interface SeriesListItem {
  ticker: string;
  title: string;
  category: string;
  frequency: string;
}

export interface BookLevel {
  price: number;
  qty: number;
}

/** Resting bids per side, best (highest) price first. */
export interface OrderBook {
  ticker: string;
  yes: BookLevel[];
  no: BookLevel[];
}

export type KalshiErrorKind =
  | "offline"
  | "rate_limited"
  | "upstream"
  | "not_found"
  | "bad_request"
  | "invalid_response"
  | "blocked";

export interface KalshiError {
  kind: KalshiErrorKind;
  message: string;
  status?: number;
}

/** Errors are values: the renderer renders them as states and never sees a throw. */
export type KalshiResult<T> =
  | { ok: true; data: T; fetchedAt: string }
  | { ok: false; error: KalshiError };

export interface CopyStrategyRequest {
  series: string;
}

export interface KalshiApi {
  searchSeries: (query: string) => Promise<KalshiResult<SeriesListItem[]>>;
  series: (ticker: string) => Promise<KalshiResult<KalshiSeries>>;
  markets: (series: string) => Promise<KalshiResult<KalshiMarket[]>>;
  market: (ticker: string) => Promise<KalshiResult<KalshiMarket>>;
  orderbook: (ticker: string) => Promise<KalshiResult<OrderBook>>;
  /** Copies `uv run nem strategy add PORTFOLIO NAME --series S`; returns the copied text. */
  copyStrategyCommand: (series: string) => Promise<KalshiResult<string>>;
}

export function strategyAddCommand(series: string): string {
  return `uv run nem strategy add PORTFOLIO NAME --series ${series}`;
}

/**
 * Asks one side can lift = the other side's resting bids at 1 - p, rounded to 0.0001 like
 * the engine (`round(1 - p, 4)` in paper_broker.py / source.py). Kalshi books hold bids only.
 */
export function impliedAsks(otherSideBids: readonly BookLevel[]): BookLevel[] {
  return otherSideBids.map((l) => ({ price: Math.round((1 - l.price) * 10_000) / 10_000, qty: l.qty }));
}
