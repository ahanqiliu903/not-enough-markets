/**
 * Test-only transport: serves recorded Kalshi responses from a folder instead of the
 * network. Enabled by `NEM_DESK_KALSHI_FIXTURES=<dir>` in an unpackaged build (the e2e
 * test sets it; see README "Explore"). File names per request:
 *   /series                         -> series.json
 *   /series/{T}                     -> series_{T}.json
 *   /markets?series_ticker={T}      -> markets_{T}.json
 *   /markets/{T}                    -> market_{T}.json
 *   /markets/{T}/orderbook          -> orderbook_{T}.json
 * A missing file answers 404. The URL still goes through the host allowlist first.
 */
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { TICKER_RE } from "@shared/kalshi";
import type { FetchLike, FetchResponse } from "./client";

function response(status: number, body: unknown): FetchResponse {
  return { status, headers: { get: () => null }, json: () => Promise.resolve(body) };
}

export function fixtureFileFor(href: string): string | null {
  const url = new URL(href);
  const path = url.pathname.replace(/^\/trade-api\/v2/, "");
  const parts = path.split("/").filter((p) => p !== "").map(decodeURIComponent);
  if (parts.some((p) => !TICKER_RE.test(p))) return null;
  const [a, b, c] = parts;
  if (a === "series" && b === undefined) return "series.json";
  if (a === "series" && b !== undefined && c === undefined) return `series_${b}.json`;
  if (a === "markets" && b === undefined) {
    const s = url.searchParams.get("series_ticker");
    return s !== null && TICKER_RE.test(s) ? `markets_${s}.json` : null;
  }
  if (a === "markets" && b !== undefined && c === undefined) return `market_${b}.json`;
  if (a === "markets" && b !== undefined && c === "orderbook") return `orderbook_${b}.json`;
  return null;
}

export function fixtureFetch(dir: string): FetchLike {
  return async (href) => {
    const file = fixtureFileFor(href);
    if (file === null) return response(404, { error: "no fixture" });
    try {
      const text = await readFile(join(dir, file), "utf8");
      return response(200, JSON.parse(text) as unknown);
    } catch {
      return response(404, { error: `no fixture ${file}` });
    }
  };
}
