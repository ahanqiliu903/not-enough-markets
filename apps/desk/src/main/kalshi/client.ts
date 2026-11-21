/**
 * Read-only client for Kalshi's public market data. GET only, no auth headers, one host.
 * Politeness: at most 5 requests/s (spaced 200ms apart), retries on 429 / 5xx / network
 * errors with exponential backoff (0.5s, 1s, 2s, like kalshi.py), short response caches.
 * Every failure comes back as a `KalshiResult` error value; nothing here throws to IPC.
 */
import {
  KALSHI_BASE_URL,
  KALSHI_HOST,
  type KalshiError,
  type KalshiMarket,
  type KalshiResult,
  type KalshiSeries,
  type OrderBook,
  type SeriesListItem,
} from "@shared/kalshi";
import {
  ParseError,
  parseMarketResponse,
  parseMarketsPage,
  parseOrderbookResponse,
  parseSeriesList,
  parseSeriesResponse,
  searchSeries,
} from "./parse";

export class HostNotAllowedError extends Error {}

const BASE_PATH = new URL(KALSHI_BASE_URL).pathname;

/** Only https://api.elections.kalshi.com/trade-api/v2/... may ever be requested. */
export function assertAllowedUrl(raw: string): URL {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new HostNotAllowedError(`not a URL: ${raw}`);
  }
  if (
    url.protocol !== "https:" ||
    url.host !== KALSHI_HOST ||
    url.username !== "" ||
    url.password !== "" ||
    !url.pathname.startsWith(`${BASE_PATH}/`)
  ) {
    throw new HostNotAllowedError(`request blocked (only ${KALSHI_BASE_URL}): ${url.origin}${url.pathname}`);
  }
  return url;
}

export interface FetchResponse {
  status: number;
  headers: { get(name: string): string | null };
  json(): Promise<unknown>;
}
export type FetchLike = (
  url: string,
  init: { method: "GET"; headers: Record<string, string>; redirect: "error"; signal?: AbortSignal },
) => Promise<FetchResponse>;

/** Spaces request starts at least `1000 / perSecond` ms apart. Slots are reserved in call order. */
export class RateLimiter {
  private next = 0;
  constructor(
    private readonly perSecond: number,
    private readonly now: () => number,
    private readonly sleep: (ms: number) => Promise<void>,
  ) {}

  async acquire(): Promise<void> {
    const t = this.now();
    const slot = Math.max(t, this.next);
    this.next = slot + 1000 / this.perSecond;
    if (slot > t) await this.sleep(slot - t);
  }
}

/** TTL cache of successful results with in-flight de-duplication. */
export class TtlCache {
  private readonly entries = new Map<string, { expires: number; value: KalshiResult<unknown> }>();
  private readonly inflight = new Map<string, Promise<KalshiResult<unknown>>>();
  constructor(
    private readonly now: () => number,
    private readonly maxEntries = 500,
  ) {}

  get<T>(key: string, ttlMs: number, load: () => Promise<KalshiResult<T>>): Promise<KalshiResult<T>> {
    const hit = this.entries.get(key);
    if (hit !== undefined && hit.expires > this.now()) return Promise.resolve(hit.value as KalshiResult<T>);
    const pending = this.inflight.get(key);
    if (pending !== undefined) return pending as Promise<KalshiResult<T>>;
    const p = load().then((value) => {
      this.inflight.delete(key);
      if (value.ok) {
        if (this.entries.size >= this.maxEntries) this.prune();
        this.entries.set(key, { expires: this.now() + ttlMs, value });
      }
      return value;
    });
    this.inflight.set(key, p);
    return p;
  }

  private prune(): void {
    const now = this.now();
    for (const [k, v] of this.entries) if (v.expires <= now) this.entries.delete(k);
    while (this.entries.size >= this.maxEntries) {
      const first = this.entries.keys().next();
      if (first.done === true) break;
      this.entries.delete(first.value);
    }
  }
}

export const TTL_MS = {
  markets: 5_000,
  market: 5_000,
  orderbook: 5_000,
  series: 10 * 60_000,
  seriesList: 60 * 60_000,
} as const;

export interface ClientOptions {
  fetch: FetchLike;
  userAgent: string;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
  perSecond?: number;
  maxRetries?: number;
  timeoutMs?: number;
  /** Max `/markets` pages per series (200 markets each). */
  maxPages?: number;
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function fail(kind: KalshiError["kind"], message: string, status?: number): { ok: false; error: KalshiError } {
  return { ok: false, error: status === undefined ? { kind, message } : { kind, message, status } };
}

export class KalshiClient {
  private readonly fetchFn: FetchLike;
  private readonly userAgent: string;
  private readonly now: () => number;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly limiter: RateLimiter;
  private readonly cache: TtlCache;
  private readonly maxRetries: number;
  private readonly timeoutMs: number;
  private readonly maxPages: number;

  constructor(opts: ClientOptions) {
    this.fetchFn = opts.fetch;
    this.userAgent = opts.userAgent;
    this.now = opts.now ?? Date.now;
    this.sleep = opts.sleep ?? defaultSleep;
    this.limiter = new RateLimiter(opts.perSecond ?? 5, this.now, this.sleep);
    this.cache = new TtlCache(this.now);
    this.maxRetries = opts.maxRetries ?? 3;
    this.timeoutMs = opts.timeoutMs ?? 10_000;
    this.maxPages = opts.maxPages ?? 5;
  }

  /** One GET with retries. Returns the parsed JSON body or an error value. */
  async getJson(path: string, params: Record<string, string> = {}): Promise<KalshiResult<unknown>> {
    let url: URL;
    try {
      url = assertAllowedUrl(`${KALSHI_BASE_URL}${path}`);
    } catch (e) {
      return fail("blocked", e instanceof Error ? e.message : String(e));
    }
    for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
    const href = url.toString();

    let last: { ok: false; error: KalshiError } = fail("offline", "no attempt made");
    let retryAfterMs = 0;
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      if (attempt > 0) await this.sleep(Math.max(500 * 2 ** (attempt - 1), retryAfterMs));
      retryAfterMs = 0;
      await this.limiter.acquire();
      let res: FetchResponse;
      try {
        res = await this.fetchFn(href, {
          method: "GET",
          headers: { Accept: "application/json", "User-Agent": this.userAgent },
          redirect: "error",
          signal: AbortSignal.timeout(this.timeoutMs),
        });
      } catch (e) {
        last = fail("offline", `GET ${path}: ${e instanceof Error ? e.message : String(e)}`);
        continue;
      }
      if (res.status === 429) {
        last = fail("rate_limited", `GET ${path}: HTTP 429 (Kalshi rate limit)`, 429);
        const retryAfter = Number(res.headers.get("retry-after"));
        // Honor Retry-After (seconds) when it asks for longer than our backoff, capped at 10s.
        if (Number.isFinite(retryAfter) && retryAfter > 0) retryAfterMs = Math.min(retryAfter * 1000, 10_000);
        continue;
      }
      if (res.status >= 500) {
        last = fail("upstream", `GET ${path}: HTTP ${res.status} from Kalshi`, res.status);
        continue;
      }
      if (res.status === 404) return fail("not_found", `GET ${path}: not found`, 404);
      if (res.status >= 400) return fail("bad_request", `GET ${path}: HTTP ${res.status}`, res.status);
      try {
        return { ok: true, data: await res.json(), fetchedAt: new Date(this.now()).toISOString() };
      } catch {
        return fail("invalid_response", `GET ${path}: response is not JSON`);
      }
    }
    const tries = this.maxRetries + 1;
    return { ok: false, error: { ...last.error, message: `${last.error.message}; gave up after ${tries} tries` } };
  }

  private async parsed<T>(path: string, params: Record<string, string>, parse: (body: unknown) => T): Promise<KalshiResult<T>> {
    const r = await this.getJson(path, params);
    if (!r.ok) return r;
    try {
      return { ok: true, data: parse(r.data), fetchedAt: r.fetchedAt };
    } catch (e) {
      if (e instanceof ParseError) return fail("invalid_response", `GET ${path}: ${e.message}`);
      throw e;
    }
  }

  series(ticker: string): Promise<KalshiResult<KalshiSeries>> {
    const path = `/series/${encodeURIComponent(ticker)}`;
    return this.cache.get(`series:${ticker}`, TTL_MS.series, () => this.parsed(path, {}, parseSeriesResponse));
  }

  private seriesList(): Promise<KalshiResult<SeriesListItem[]>> {
    return this.cache.get("seriesList", TTL_MS.seriesList, () => this.parsed("/series", {}, parseSeriesList));
  }

  async searchSeries(query: string): Promise<KalshiResult<SeriesListItem[]>> {
    const all = await this.seriesList();
    if (!all.ok) return all;
    return { ok: true, data: searchSeries(all.data, query), fetchedAt: all.fetchedAt };
  }

  markets(series: string): Promise<KalshiResult<KalshiMarket[]>> {
    return this.cache.get(`markets:${series}`, TTL_MS.markets, async () => {
      const out: KalshiMarket[] = [];
      let cursor = "";
      let fetchedAt = "";
      for (let page = 0; page < this.maxPages; page++) {
        const params: Record<string, string> = { series_ticker: series, status: "open", limit: "200" };
        if (cursor !== "") params["cursor"] = cursor;
        const r = await this.parsed("/markets", params, parseMarketsPage);
        if (!r.ok) return r;
        out.push(...r.data.markets);
        fetchedAt = r.fetchedAt;
        cursor = r.data.cursor;
        if (cursor === "") break;
      }
      return { ok: true, data: out, fetchedAt };
    });
  }

  market(ticker: string): Promise<KalshiResult<KalshiMarket>> {
    const path = `/markets/${encodeURIComponent(ticker)}`;
    return this.cache.get(`market:${ticker}`, TTL_MS.market, () => this.parsed(path, {}, parseMarketResponse));
  }

  orderbook(ticker: string): Promise<KalshiResult<OrderBook>> {
    const path = `/markets/${encodeURIComponent(ticker)}/orderbook`;
    return this.cache.get(`orderbook:${ticker}`, TTL_MS.orderbook, () =>
      this.parsed(path, {}, (body) => parseOrderbookResponse(ticker, body)),
    );
  }
}
