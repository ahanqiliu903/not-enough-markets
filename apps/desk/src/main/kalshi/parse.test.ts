// @vitest-environment node
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { impliedAsks } from "@shared/kalshi";
import {
  ask,
  bid,
  ParseError,
  parseMarketResponse,
  parseMarketsPage,
  parseOrderbook,
  parseOrderbookResponse,
  parseSeriesList,
  parseSeriesResponse,
  price,
  searchSeries,
} from "./parse";

// Recorded API responses shared with the Python client's tests (repo root tests/fixtures/kalshi).
const repoFixtures = join(import.meta.dirname, "../../../../../tests/fixtures/kalshi");
// Desk-only recordings (series, single orderbook, rules text), also served in e2e.
const deskFixtures = join(import.meta.dirname, "../../../e2e/fixtures/kalshi");
const load = (dir: string, name: string): unknown => JSON.parse(readFileSync(join(dir, name), "utf8")) as unknown;

describe("prices (mirrors kalshi.py _price/_bid/_ask)", () => {
  it("parses dollar strings and treats 0 bid / 1 ask as missing", () => {
    expect(price("0.9320")).toBe(0.932);
    expect(price("")).toBeNull();
    expect(price(null)).toBeNull();
    expect(bid("0.0000")).toBeNull();
    expect(bid("0.0100")).toBe(0.01);
    expect(ask("1.0000")).toBeNull();
    expect(ask("0.9990")).toBe(0.999);
  });

  it("rejects non-numeric prices like float() would", () => {
    expect(() => price("abc")).toThrow(ParseError);
  });
});

describe("markets_open.json", () => {
  const page = parseMarketsPage(load(repoFixtures, "markets_open.json"));

  it("parses the open market", () => {
    expect(page.cursor).toBe("");
    expect(page.markets).toHaveLength(1);
    const m = page.markets[0];
    expect(m).toMatchObject({
      ticker: "KXBTC15M-26OCT081430-30",
      eventTicker: "KXBTC15M-26OCT081430",
      status: "active",
      openTime: "2026-10-08T18:15:00.000Z",
      closeTime: "2026-10-08T18:30:00.000Z",
      yesBid: 0.73,
      yesAsk: 0.74,
      noBid: 0.26,
      noAsk: 0.27,
      lastPrice: 0.74,
      result: null,
      settledAt: null,
      strikeType: "greater_or_equal",
      floorStrike: 80639.88,
      capStrike: null,
      customStrike: false,
      canCloseEarly: true,
      volume: 93868.45,
      openInterest: 79680.11,
    });
  });
});

describe("market_settled.json", () => {
  it("parses a finalized market: no bid / no ask become null, result and settlement time kept", () => {
    const m = parseMarketResponse(load(repoFixtures, "market_settled.json"));
    expect(m.status).toBe("finalized");
    expect(m.yesBid).toBeNull();
    expect(m.yesAsk).toBeNull();
    expect(m.noBid).toBeNull();
    expect(m.noAsk).toBeNull();
    expect(m.result).toBe("no");
    expect(m.settledAt).toBe("2026-10-08T18:15:03.694Z");
  });
});

describe("orderbooks.json", () => {
  it("sorts each side best (highest) first and parses fractional sizes", () => {
    const body = load(repoFixtures, "orderbooks.json") as { orderbooks: { ticker: string; orderbook_fp: unknown }[] };
    const first = body.orderbooks[0];
    if (first === undefined) throw new Error("fixture empty");
    const book = parseOrderbook(first.ticker, first.orderbook_fp);
    expect(book.yes.map((l) => l.price)).toEqual([0.82, 0.81, 0.8, 0.79, 0.78, 0.77]);
    expect(book.no[0]).toEqual({ price: 0.17, qty: 12988.71 });
    expect(book.yes[0]).toEqual({ price: 0.82, qty: 1784.35 });
  });

  it("derives asks like the paper broker (round(1 - p, 4))", () => {
    expect(impliedAsks([{ price: 0.17, qty: 5 }, { price: 0.068, qty: 1 }])).toEqual([
      { price: 0.83, qty: 5 },
      { price: 0.932, qty: 1 },
    ]);
  });

  it("treats a missing side as empty and rejects malformed levels", () => {
    expect(parseOrderbook("X", {})).toEqual({ ticker: "X", yes: [], no: [] });
    expect(parseOrderbook("X", null)).toEqual({ ticker: "X", yes: [], no: [] });
    expect(() => parseOrderbook("X", { yes_dollars: [["0.5"]] })).toThrow(ParseError);
  });
});

describe("desk recordings (live API, 2026-11-20)", () => {
  it("parses a single-market orderbook response", () => {
    const book = parseOrderbookResponse("KXHIGHNY-26NOV21-B62.5", load(deskFixtures, "orderbook_KXHIGHNY-26NOV21-B62.5.json"));
    expect(book.yes.length).toBeGreaterThan(0);
    for (const side of [book.yes, book.no]) {
      for (let i = 1; i < side.length; i++) expect(side[i - 1]?.price).toBeGreaterThan(side[i]?.price ?? 1);
    }
  });

  it("parses a series with settlement sources and important info", () => {
    const s = parseSeriesResponse(load(deskFixtures, "series_KXHIGHNY.json"));
    expect(s.ticker).toBe("KXHIGHNY");
    expect(s.settlementSources).toEqual([{ name: "The Weather Company", url: "https://weather.com/kalshi" }]);
    expect(s.importantInfo).toContain("last fair price");
  });

  it("parses temperature markets with rules and between/less/greater strikes", () => {
    const { markets } = parseMarketsPage(load(deskFixtures, "markets_KXHIGHNY.json"));
    const byTicker = (t: string) => markets.find((m) => m.ticker === t);
    expect(byTicker("KXHIGHNY-26NOV21-T65")).toMatchObject({ strikeType: "greater", floorStrike: 65, capStrike: null });
    expect(byTicker("KXHIGHNY-26NOV21-T58")).toMatchObject({ strikeType: "less", floorStrike: null, capStrike: 58 });
    expect(byTicker("KXHIGHNY-26NOV21-B62.5")).toMatchObject({ strikeType: "between", floorStrike: 62, capStrike: 63 });
    expect(markets[0]?.rulesPrimary).toContain("The Weather Company");
  });

  it("flags strike_type custom (coin race) but not custom_strike metadata on BTC", () => {
    const raw = load(deskFixtures, "markets_KXBTC15M.json") as { markets: Record<string, unknown>[] };
    const btc = raw.markets[0];
    expect(btc?.["custom_strike"]).toBeTruthy();
    expect(parseMarketsPage(raw).markets[0]?.customStrike).toBe(false);
    const race = parseMarketsPage({ markets: [{ ...btc, strike_type: "custom", floor_strike: null }] });
    expect(race.markets[0]).toMatchObject({ customStrike: true, strikeType: null, floorStrike: null });
  });

  it("slims and searches the series list", () => {
    const items = parseSeriesList(load(deskFixtures, "series.json"));
    expect(items.length).toBeGreaterThan(2);
    expect(searchSeries(items, "kxhighny")[0]?.ticker).toBe("KXHIGHNY");
    expect(searchSeries(items, "btc").map((s) => s.ticker)).toContain("KXBTC15M");
    expect(searchSeries(items, "  ")).toEqual([]);
  });
});
