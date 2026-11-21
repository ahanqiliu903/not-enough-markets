/**
 * Wire-format parsing, mirroring `src/nem/market/kalshi.py` (keep them in step):
 * - Prices are dollar strings ("0.9320"), sizes fractional strings ("817.01").
 * - A bid of 0 means "no bid" and an ask of 1 means "no ask"; both become null.
 * - Orderbook ladders (`orderbook_fp.yes_dollars` / `no_dollars`) are bids per side in
 *   ascending price order; we return best (highest) first.
 * Anything malformed throws `ParseError`, which the client turns into an error value.
 */
import {
  STRIKE_TYPES,
  type BookLevel,
  type KalshiMarket,
  type KalshiSeries,
  type OrderBook,
  type SeriesListItem,
  type SettlementSource,
  type StrikeType,
} from "@shared/kalshi";

export class ParseError extends Error {}

type Obj = Record<string, unknown>;

function isObj(v: unknown): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function obj(v: unknown, what: string): Obj {
  if (!isObj(v)) throw new ParseError(`${what}: expected an object`);
  return v;
}

function str(m: Obj, key: string): string {
  const v = m[key];
  if (typeof v !== "string") throw new ParseError(`${key}: expected a string`);
  return v;
}

function optStr(m: Obj, key: string): string | null {
  const v = m[key];
  return typeof v === "string" && v !== "" ? v : null;
}

/** Python `float(s)`: numeric strings (and numbers) only. */
function toFloat(v: unknown, what: string): number {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN;
  if (!Number.isFinite(n)) throw new ParseError(`${what}: not a number: ${JSON.stringify(v)}`);
  return n;
}

/** `_price`: None/"" -> null, else float. */
export function price(v: unknown, what = "price"): number | null {
  return v === undefined || v === null || v === "" ? null : toFloat(v, what);
}

/** `_bid`: 0 (or below) means no bid. */
export function bid(v: unknown, what = "bid"): number | null {
  const p = price(v, what);
  return p === null || p <= 0 ? null : p;
}

/** `_ask`: 1 (or above) means no ask. */
export function ask(v: unknown, what = "ask"): number | null {
  const p = price(v, what);
  return p === null || p >= 1 ? null : p;
}

/** `_number`: int/float/non-empty string -> float, anything else null. */
export function number(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** `_time`: ISO 8601, normalized to `Date.toISOString()`. */
function time(v: unknown, what: string): string {
  if (typeof v !== "string") throw new ParseError(`${what}: expected a timestamp`);
  const t = Date.parse(v);
  if (Number.isNaN(t)) throw new ParseError(`${what}: bad timestamp ${JSON.stringify(v)}`);
  return new Date(t).toISOString();
}

function strikeType(v: unknown): StrikeType | null {
  return (STRIKE_TYPES as readonly unknown[]).includes(v) ? (v as StrikeType) : null;
}

export function parseMarket(raw: unknown): KalshiMarket {
  const m = obj(raw, "market");
  const result = m["result"];
  const settlementTs = m["settlement_ts"];
  return {
    ticker: str(m, "ticker"),
    eventTicker: str(m, "event_ticker"),
    title: optStr(m, "title") ?? "",
    yesSubTitle: optStr(m, "yes_sub_title") ?? "",
    noSubTitle: optStr(m, "no_sub_title") ?? "",
    status: str(m, "status"),
    openTime: time(m["open_time"], "open_time"),
    closeTime: time(m["close_time"], "close_time"),
    yesBid: bid(m["yes_bid_dollars"], "yes_bid_dollars"),
    yesAsk: ask(m["yes_ask_dollars"], "yes_ask_dollars"),
    noBid: bid(m["no_bid_dollars"], "no_bid_dollars"),
    noAsk: ask(m["no_ask_dollars"], "no_ask_dollars"),
    lastPrice: price(m["last_price_dollars"], "last_price_dollars"),
    result: result === "yes" || result === "no" ? result : null,
    settledAt:
      typeof settlementTs === "string" && settlementTs !== "" ? time(settlementTs, "settlement_ts") : null,
    strikeType: strikeType(m["strike_type"]),
    floorStrike: number(m["floor_strike"]),
    capStrike: number(m["cap_strike"]),
    // `custom_strike` is also set on plain markets (e.g. {"round_digits": "2"}); the strike
    // type says whether YES depends on something floor/cap can't express.
    customStrike: m["strike_type"] === "custom",
    canCloseEarly: m["can_close_early"] === true,
    rulesPrimary: optStr(m, "rules_primary"),
    rulesSecondary: optStr(m, "rules_secondary"),
    volume: number(m["volume_fp"]),
    openInterest: number(m["open_interest_fp"]),
  };
}

/** `/markets` page -> markets plus the next cursor ("" = last page). */
export function parseMarketsPage(raw: unknown): { markets: KalshiMarket[]; cursor: string } {
  const body = obj(raw, "markets response");
  const list = body["markets"] ?? [];
  if (!Array.isArray(list)) throw new ParseError("markets: expected a list");
  const cursor = body["cursor"];
  return { markets: list.map(parseMarket), cursor: typeof cursor === "string" ? cursor : "" };
}

export function parseMarketResponse(raw: unknown): KalshiMarket {
  return parseMarket(obj(raw, "market response")["market"]);
}

function side(levels: unknown, what: string): BookLevel[] {
  if (levels === undefined || levels === null) return [];
  if (!Array.isArray(levels)) throw new ParseError(`${what}: expected a list`);
  const parsed = levels.map((lv, i) => {
    if (!Array.isArray(lv) || lv.length < 2) throw new ParseError(`${what}[${i}]: expected [price, qty]`);
    return { price: toFloat(lv[0], `${what}[${i}] price`), qty: toFloat(lv[1], `${what}[${i}] qty`) };
  });
  return parsed.sort((a, b) => b.price - a.price);
}

/** `{"yes_dollars": [["0.77", "9015.74"], ...], "no_dollars": [...]}` -> best-first. */
export function parseOrderbook(ticker: string, book: unknown): OrderBook {
  const b = isObj(book) ? book : {};
  return { ticker, yes: side(b["yes_dollars"], "yes_dollars"), no: side(b["no_dollars"], "no_dollars") };
}

/** `/markets/{ticker}/orderbook` -> `{"orderbook_fp": {...}}`. */
export function parseOrderbookResponse(ticker: string, raw: unknown): OrderBook {
  return parseOrderbook(ticker, obj(raw, "orderbook response")["orderbook_fp"]);
}

function sources(v: unknown): SettlementSource[] {
  if (!Array.isArray(v)) return [];
  return v.filter(isObj).flatMap((s) => {
    const name = s["name"];
    if (typeof name !== "string" || name === "") return [];
    const url = s["url"];
    return [{ name, url: typeof url === "string" && url !== "" ? url : null }];
  });
}

export function parseSeries(raw: unknown): KalshiSeries {
  const s = obj(raw, "series");
  const meta = s["product_metadata"];
  const info = isObj(meta) ? meta["important_info"] : undefined;
  const markdown = isObj(info) ? info["markdown"] : undefined;
  const tags = s["tags"];
  return {
    ticker: str(s, "ticker"),
    title: optStr(s, "title") ?? "",
    category: optStr(s, "category") ?? "",
    frequency: optStr(s, "frequency") ?? "",
    settlementSources: sources(s["settlement_sources"]),
    contractTermsUrl: optStr(s, "contract_terms_url"),
    importantInfo: typeof markdown === "string" && markdown !== "" ? markdown : null,
    tags: Array.isArray(tags) ? tags.filter((t): t is string => typeof t === "string") : [],
  };
}

export function parseSeriesResponse(raw: unknown): KalshiSeries {
  return parseSeries(obj(raw, "series response")["series"]);
}

/** `/series` -> slim list items (the full response is ~20 MB; keep only what search needs). */
export function parseSeriesList(raw: unknown): SeriesListItem[] {
  const list = obj(raw, "series list")["series"] ?? [];
  if (!Array.isArray(list)) throw new ParseError("series: expected a list");
  return list.filter(isObj).flatMap((s) => {
    const ticker = s["ticker"];
    if (typeof ticker !== "string" || ticker === "") return [];
    return [
      {
        ticker,
        title: optStr(s, "title") ?? "",
        category: optStr(s, "category") ?? "",
        frequency: optStr(s, "frequency") ?? "",
      },
    ];
  });
}

/** Search ranking: exact ticker, ticker prefix, then substring of ticker/title/category. */
export function searchSeries(items: readonly SeriesListItem[], query: string, limit = 50): SeriesListItem[] {
  const q = query.trim().toLowerCase();
  if (q === "") return [];
  const scored: { item: SeriesListItem; score: number }[] = [];
  for (const item of items) {
    const t = item.ticker.toLowerCase();
    let score = -1;
    if (t === q) score = 0;
    else if (t.startsWith(q)) score = 1;
    else if (t.includes(q)) score = 2;
    else if (item.title.toLowerCase().includes(q)) score = 3;
    else if (item.category.toLowerCase().includes(q)) score = 4;
    if (score >= 0) scored.push({ item, score });
  }
  scored.sort((a, b) => a.score - b.score || a.item.ticker.localeCompare(b.item.ticker));
  return scored.slice(0, limit).map((s) => s.item);
}
