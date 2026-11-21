/**
 * `kalshi:*` IPC handlers (DESK-024). Requests come from the renderer, so every argument is
 * validated here before it reaches the client.
 */
import { app, clipboard } from "electron";
import {
  isTicker,
  strategyAddCommand,
  type KalshiError,
  type KalshiMarket,
  type KalshiResult,
  type KalshiSeries,
  type OrderBook,
  type SeriesListItem,
} from "@shared/kalshi";
import { KalshiClient, type FetchLike } from "./client";
import { fixtureFetch } from "./fixtures";

export const FIXTURES_ENV = "NEM_DESK_KALSHI_FIXTURES";

let client: KalshiClient | undefined;

function getClient(): KalshiClient {
  if (client !== undefined) return client;
  const fixtures = process.env[FIXTURES_ENV];
  // Recorded fixtures only in dev/test builds; a packaged app always talks to Kalshi.
  const fetchFn: FetchLike =
    fixtures !== undefined && fixtures !== "" && !app.isPackaged
      ? fixtureFetch(fixtures)
      : (url, init) => fetch(url, init);
  client = new KalshiClient({ fetch: fetchFn, userAgent: `NEM Desk/${app.getVersion()}` });
  return client;
}

function badRequest(message: string): { ok: false; error: KalshiError } {
  return { ok: false, error: { kind: "bad_request", message } };
}

const MAX_QUERY = 64;

export const kalshiHandlers = {
  searchSeries: (query: unknown): Promise<KalshiResult<SeriesListItem[]>> | KalshiResult<SeriesListItem[]> => {
    if (typeof query !== "string" || query.length > MAX_QUERY) return badRequest("search: expected a short string");
    return getClient().searchSeries(query);
  },
  series: (ticker: unknown): Promise<KalshiResult<KalshiSeries>> | KalshiResult<KalshiSeries> =>
    isTicker(ticker) ? getClient().series(ticker) : badRequest("series: bad ticker"),
  markets: (series: unknown): Promise<KalshiResult<KalshiMarket[]>> | KalshiResult<KalshiMarket[]> =>
    isTicker(series) ? getClient().markets(series) : badRequest("markets: bad series ticker"),
  market: (ticker: unknown): Promise<KalshiResult<KalshiMarket>> | KalshiResult<KalshiMarket> =>
    isTicker(ticker) ? getClient().market(ticker) : badRequest("market: bad ticker"),
  orderbook: (ticker: unknown): Promise<KalshiResult<OrderBook>> | KalshiResult<OrderBook> =>
    isTicker(ticker) ? getClient().orderbook(ticker) : badRequest("orderbook: bad ticker"),
  /**
   * Main builds the text from a validated ticker and writes it with Electron's clipboard, so
   * the renderer keeps every permission denied and can't put arbitrary text on the clipboard.
   */
  copyStrategyCommand: async (series: unknown): Promise<KalshiResult<string>> => {
    if (!isTicker(series)) return badRequest("copy: bad series ticker");
    const text = strategyAddCommand(series);
    try {
      await clipboard.writeText(text);
    } catch (e) {
      return { ok: false, error: { kind: "blocked", message: `clipboard: ${e instanceof Error ? e.message : String(e)}` } };
    }
    return { ok: true, data: text, fetchedAt: new Date().toISOString() };
  },
};
