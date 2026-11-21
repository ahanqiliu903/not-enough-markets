// @vitest-environment node
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { assertAllowedUrl, HostNotAllowedError, KalshiClient, RateLimiter, type FetchLike, type FetchResponse } from "./client";
import { fixtureFetch, fixtureFileFor } from "./fixtures";

const repoFixtures = join(import.meta.dirname, "../../../../../tests/fixtures/kalshi");
const deskFixtures = join(import.meta.dirname, "../../../e2e/fixtures/kalshi");
const marketsOpen = JSON.parse(readFileSync(join(repoFixtures, "markets_open.json"), "utf8")) as unknown;

function res(status: number, body: unknown = {}, headers: Record<string, string> = {}): FetchResponse {
  return { status, headers: { get: (n) => headers[n.toLowerCase()] ?? null }, json: () => Promise.resolve(body) };
}

/** A client whose clock and sleeps are fake: sleeps advance `clock` instantly and are recorded. */
function fakeClient(fetchFn: FetchLike, perSecond = 5) {
  let clock = 1_000_000;
  const sleeps: number[] = [];
  const client = new KalshiClient({
    fetch: fetchFn,
    userAgent: "NEM Desk/test",
    now: () => clock,
    sleep: (ms) => {
      sleeps.push(ms);
      clock += ms;
      return Promise.resolve();
    },
    perSecond,
  });
  return { client, sleeps, advance: (ms: number) => (clock += ms) };
}

describe("host allowlist", () => {
  it("allows only https://api.elections.kalshi.com/trade-api/v2/...", () => {
    expect(assertAllowedUrl("https://api.elections.kalshi.com/trade-api/v2/markets").host).toBe("api.elections.kalshi.com");
    for (const bad of [
      "https://example.com/trade-api/v2/markets",
      "http://api.elections.kalshi.com/trade-api/v2/markets",
      "https://external-api.demo.kalshi.co/trade-api/v2/markets",
      "https://api.elections.kalshi.com.evil.io/trade-api/v2/markets",
      "https://user:pw@api.elections.kalshi.com/trade-api/v2/markets",
      "https://api.elections.kalshi.com/trade-api/v1/markets",
      "https://api.elections.kalshi.com/portfolio/orders",
      "not a url",
    ]) {
      expect(() => assertAllowedUrl(bad), bad).toThrow(HostNotAllowedError);
    }
  });

  it("returns a blocked error value when a path escapes the base", async () => {
    const fetchFn = vi.fn<FetchLike>();
    const { client } = fakeClient(fetchFn);
    const r = await client.getJson("/../../portfolio/orders");
    expect(r).toMatchObject({ ok: false, error: { kind: "blocked" } });
    expect(fetchFn).not.toHaveBeenCalled();
  });
});

describe("requests", () => {
  it("sends GET with User-Agent, no auth headers, no redirects", async () => {
    const fetchFn = vi.fn<FetchLike>(() => Promise.resolve(res(200, marketsOpen)));
    const { client } = fakeClient(fetchFn);
    const r = await client.markets("KXBTC15M");
    expect(r.ok).toBe(true);
    const [url, init] = fetchFn.mock.calls[0] ?? [];
    expect(url).toBe("https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXBTC15M&status=open&limit=200");
    expect(init?.method).toBe("GET");
    expect(init?.redirect).toBe("error");
    expect(init?.headers).toEqual({ Accept: "application/json", "User-Agent": "NEM Desk/test" });
  });

  it("follows cursors across pages", async () => {
    const page1 = { cursor: "abc", markets: (marketsOpen as { markets: unknown[] }).markets };
    const fetchFn = vi.fn<FetchLike>((url) => Promise.resolve(res(200, url.includes("cursor=abc") ? marketsOpen : page1)));
    const { client } = fakeClient(fetchFn);
    const r = await client.markets("KXBTC15M");
    expect(fetchFn).toHaveBeenCalledTimes(2);
    expect(r.ok && r.data.length).toBe(2);
  });
});

describe("retries and errors as values", () => {
  it("retries 429 and 5xx with backoff 0.5s, 1s, then succeeds", async () => {
    const replies = [res(429), res(503), res(200, marketsOpen)];
    const fetchFn = vi.fn<FetchLike>(() => Promise.resolve(replies.shift() ?? res(500)));
    const { client, sleeps } = fakeClient(fetchFn);
    const r = await client.markets("KXBTC15M");
    expect(r.ok).toBe(true);
    expect(fetchFn).toHaveBeenCalledTimes(3);
    expect(sleeps.filter((s) => s >= 500)).toEqual([500, 1000]);
  });

  it("honors Retry-After when longer than the backoff", async () => {
    const replies = [res(429, {}, { "retry-after": "3" }), res(200, marketsOpen)];
    const { client, sleeps } = fakeClient(() => Promise.resolve(replies.shift() ?? res(500)));
    await client.markets("KXBTC15M");
    expect(sleeps).toContain(3000);
  });

  it("gives up after 4 tries: rate_limited, upstream, offline", async () => {
    const cases: [FetchLike, string, number | undefined][] = [
      [() => Promise.resolve(res(429)), "rate_limited", 429],
      [() => Promise.resolve(res(502)), "upstream", 502],
      [() => Promise.reject(new TypeError("fetch failed")), "offline", undefined],
    ];
    for (const [fetchFn, kind, status] of cases) {
      const spy = vi.fn(fetchFn);
      const { client } = fakeClient(spy);
      const r = await client.market("KXBTC15M-26OCT081430-30");
      expect(r.ok).toBe(false);
      if (!r.ok) {
        expect(r.error.kind).toBe(kind);
        expect(r.error.status).toBe(status);
        expect(r.error.message).toContain("gave up after 4 tries");
      }
      expect(spy).toHaveBeenCalledTimes(4);
    }
  });

  it("does not retry 404 / 4xx and reports parse failures as invalid_response", async () => {
    const f404 = vi.fn<FetchLike>(() => Promise.resolve(res(404)));
    const a = await fakeClient(f404).client.series("NOPE");
    expect(a).toMatchObject({ ok: false, error: { kind: "not_found", status: 404 } });
    expect(f404).toHaveBeenCalledTimes(1);

    const f400 = vi.fn<FetchLike>(() => Promise.resolve(res(400)));
    expect(await fakeClient(f400).client.series("X")).toMatchObject({ ok: false, error: { kind: "bad_request" } });

    const bad = { markets: [{ ticker: "X", event_ticker: "E", status: "active", open_time: "nope", close_time: "x" }] };
    const r = await fakeClient(() => Promise.resolve(res(200, bad))).client.markets("X");
    expect(r).toMatchObject({ ok: false, error: { kind: "invalid_response" } });

    const notJson: FetchResponse = { status: 200, headers: { get: () => null }, json: () => Promise.reject(new SyntaxError("x")) };
    expect(await fakeClient(() => Promise.resolve(notJson)).client.series("X")).toMatchObject({
      ok: false,
      error: { kind: "invalid_response" },
    });
  });
});

describe("cache", () => {
  it("serves repeats from cache within the TTL and refetches after", async () => {
    const fetchFn = vi.fn<FetchLike>(() => Promise.resolve(res(200, marketsOpen)));
    const { client, advance } = fakeClient(fetchFn);
    await Promise.all([client.markets("KXBTC15M"), client.markets("KXBTC15M")]);
    await client.markets("KXBTC15M");
    expect(fetchFn).toHaveBeenCalledTimes(1);
    advance(6_000);
    await client.markets("KXBTC15M");
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it("does not cache errors", async () => {
    const replies = [res(404), res(200, marketsOpen)];
    const fetchFn = vi.fn<FetchLike>(() => Promise.resolve(replies.shift() ?? res(500)));
    const { client } = fakeClient(fetchFn);
    expect((await client.markets("KXBTC15M")).ok).toBe(false);
    expect((await client.markets("KXBTC15M")).ok).toBe(true);
  });
});

describe("RateLimiter (fake timers)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts at most 5 requests in any 1s window", async () => {
    const limiter = new RateLimiter(5, () => Date.now(), (ms) => new Promise((r) => setTimeout(r, ms)));
    const starts: number[] = [];
    const t0 = Date.now();
    const all = Array.from({ length: 12 }, () => limiter.acquire().then(() => starts.push(Date.now() - t0)));
    await vi.advanceTimersByTimeAsync(5_000);
    await Promise.all(all);
    expect(starts).toEqual([0, 200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200]);
    for (const s of starts) expect(starts.filter((x) => x >= s && x < s + 1000).length).toBeLessThanOrEqual(5);
  });

  it("spaces real client requests through the limiter", async () => {
    const times: number[] = [];
    const client = new KalshiClient({
      fetch: () => {
        times.push(Date.now());
        return Promise.resolve(res(200, { series: { ticker: "S" } }));
      },
      userAgent: "t",
    });
    const p = Promise.all(["A", "B", "C"].map((t) => client.series(t)));
    await vi.advanceTimersByTimeAsync(1_000);
    await p;
    expect(times.map((t) => t - (times[0] ?? 0))).toEqual([0, 200, 400]);
  });
});

describe("fixture transport (e2e switch)", () => {
  it("maps API paths to recorded files", () => {
    const base = "https://api.elections.kalshi.com/trade-api/v2";
    expect(fixtureFileFor(`${base}/series`)).toBe("series.json");
    expect(fixtureFileFor(`${base}/series/KXHIGHNY`)).toBe("series_KXHIGHNY.json");
    expect(fixtureFileFor(`${base}/markets?series_ticker=KXBTC15M&status=open`)).toBe("markets_KXBTC15M.json");
    expect(fixtureFileFor(`${base}/markets/KXHIGHNY-26NOV21-B62.5`)).toBe("market_KXHIGHNY-26NOV21-B62.5.json");
    expect(fixtureFileFor(`${base}/markets/X/orderbook`)).toBe("orderbook_X.json");
    expect(fixtureFileFor(`${base}/markets/..%2F..%2Fetc/orderbook`)).toBeNull();
  });

  it("serves fixtures through the normal client and 404s missing ones", async () => {
    const { client } = fakeClient(fixtureFetch(deskFixtures));
    const s = await client.series("KXBTC15M");
    expect(s.ok && s.data.settlementSources[0]?.name).toBe("CF Benchmarks");
    expect(await client.series("KXNOPE")).toMatchObject({ ok: false, error: { kind: "not_found" } });
  });
});
