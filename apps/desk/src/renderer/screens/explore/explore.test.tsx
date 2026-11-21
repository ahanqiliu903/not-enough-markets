import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NemApi } from "@shared/ipc";
import type { KalshiApi, KalshiResult } from "@shared/kalshi";
import { parseMarketResponse, parseMarketsPage, parseOrderbookResponse, parseSeriesList, parseSeriesResponse, searchSeries } from "../../../main/kalshi/parse";
import btcMarkets from "../../../../e2e/fixtures/kalshi/markets_KXBTC15M.json";
import nyMarket from "../../../../e2e/fixtures/kalshi/market_KXHIGHNY-26NOV21-B62.5.json";
import nyMarkets from "../../../../e2e/fixtures/kalshi/markets_KXHIGHNY.json";
import nyBook from "../../../../e2e/fixtures/kalshi/orderbook_KXHIGHNY-26NOV21-B62.5.json";
import seriesList from "../../../../e2e/fixtures/kalshi/series.json";
import nySeries from "../../../../e2e/fixtures/kalshi/series_KXHIGHNY.json";
import { useNav } from "../../state/nav";
import { exploreQueryCommand, exploreSeedCommands } from "./commands";
import { countdown, formatEt, formatPrice, yesMeans } from "./format";
import ExploreScreen from "./index";
import { sortMarkets } from "./MarketList";

// Fixtures recorded 2026-11-20 ~20:00Z; pin the clock so countdowns are deterministic.
const NOW = Date.parse("2026-11-20T20:05:00Z");
const NY_MARKET = "KXHIGHNY-26NOV21-B62.5";

const ok = <T,>(data: T): Promise<KalshiResult<T>> => Promise.resolve({ ok: true, data, fetchedAt: new Date().toISOString() });
const notFound = <T,>(): Promise<KalshiResult<T>> => Promise.resolve({ ok: false, error: { kind: "not_found", message: "no" } });

function must<T>(v: T | null | undefined): T {
  if (v === null || v === undefined) throw new Error("expected a value");
  return v;
}

function mockKalshi(overrides: Partial<KalshiApi> = {}): KalshiApi {
  const api: KalshiApi = {
    searchSeries: vi.fn<KalshiApi["searchSeries"]>((q: string) => ok(searchSeries(parseSeriesList(seriesList), q))),
    series: vi.fn<KalshiApi["series"]>((t: string) => (t === "KXHIGHNY" ? ok(parseSeriesResponse(nySeries)) : notFound())),
    markets: vi.fn<KalshiApi["markets"]>((s: string) =>
      s === "KXHIGHNY" ? ok(parseMarketsPage(nyMarkets).markets) : s === "KXBTC15M" ? ok(parseMarketsPage(btcMarkets).markets) : notFound(),
    ),
    market: vi.fn<KalshiApi["market"]>((t: string) => (t === NY_MARKET ? ok(parseMarketResponse(nyMarket)) : notFound())),
    orderbook: vi.fn<KalshiApi["orderbook"]>((t: string) => (t === NY_MARKET ? ok(parseOrderbookResponse(t, nyBook)) : notFound())),
    copyStrategyCommand: vi.fn<KalshiApi["copyStrategyCommand"]>((s: string) => ok(`uv run nem strategy add PORTFOLIO NAME --series ${s}`)),
    ...overrides,
  };
  (window as { nem?: NemApi }).nem = { kalshi: api, platform: "linux" } as unknown as NemApi;
  return api;
}

function renderExplore(params: Record<string, string> = {}) {
  act(() => useNav.setState({ screen: "explore", params, history: [] }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ExploreScreen />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => {
  vi.useRealTimers();
  delete (window as { nem?: NemApi }).nem;
});

describe("format", () => {
  it("formats prices, countdowns and ET times", () => {
    expect(formatPrice(0.932)).toBe("93.2c");
    expect(formatPrice(0.74)).toBe("74c");
    expect(formatPrice(null)).toBe("--");
    expect(countdown("2026-11-20T20:15:00Z", NOW)).toBe("10m 00s");
    expect(countdown("2026-11-22T06:00:00Z", NOW)).toBe("33h 55m");
    expect(countdown("2026-11-20T20:00:00Z", NOW)).toBe("closed");
    expect(formatEt("2026-11-22T06:00:00Z")).toBe("Nov 22, 1:00 AM EST");
  });

  it("says what YES means from the strike", () => {
    const ms = parseMarketsPage(nyMarkets).markets;
    expect(yesMeans(must(ms.find((m) => m.ticker === "KXHIGHNY-26NOV21-T65")))).toContain("> 65");
    expect(yesMeans(must(ms.find((m) => m.ticker === "KXHIGHNY-26NOV21-T58")))).toContain("< 58");
    expect(yesMeans(must(ms.find((m) => m.ticker === NY_MARKET)))).toContain("62 to 63");
  });

  it("sorts closing soon first", () => {
    const sorted = sortMarkets(parseMarketsPage(nyMarkets).markets, "closing");
    expect(sorted[0]?.ticker.startsWith("KXHIGHNY-26NOV20")).toBe(true);
    expect(sorted.at(-1)?.ticker.startsWith("KXHIGHNY-26NOV21")).toBe(true);
  });
});

describe("palette", () => {
  it("has explore commands for seeded series and any typed series", () => {
    expect(exploreSeedCommands().map((c) => c.title)).toContain("explore KXBTC15M");
    const cmd = exploreQueryCommand("explore kxfoo");
    expect(cmd?.title).toBe("explore KXFOO");
    cmd?.run();
    expect(useNav.getState()).toMatchObject({ screen: "explore", params: { series: "KXFOO" } });
    expect(exploreQueryCommand("explore ../etc")).toBeNull();
    expect(exploreQueryCommand("trades")).toBeNull();
  });
});

describe("Explore screen", () => {
  it("shows category tabs and series tiles with open counts and next close", async () => {
    mockKalshi();
    renderExplore();
    const tabs = screen.getByRole("tablist", { name: "Categories" });
    expect(within(tabs).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Crypto 15m", "Daily high", "Daily low", "Search"]);
    const btc = await screen.findByRole("button", { name: "Open series KXBTC15M" });
    expect(await within(btc).findByText("1")).toBeInTheDocument();
    expect(within(btc).getByText("10m 00s")).toBeInTheDocument();
    // Unknown series answer not_found: the tile says so instead of crashing.
    const eth = screen.getByRole("button", { name: "Open series KXETH15M" });
    expect(await within(eth).findByText("not found")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Daily high" }));
    const ny = await screen.findByRole("button", { name: "Open series KXHIGHNY" });
    expect(await within(ny).findByText("12")).toBeInTheDocument();
    fireEvent.click(ny);
    expect(useNav.getState().params).toMatchObject({ series: "KXHIGHNY", tab: "temp-high" });
  });

  it("shows a tile rules card with the predictor note on hover", async () => {
    mockKalshi();
    renderExplore({ tab: "temp-low" });
    const tile = (await screen.findByRole("button", { name: "Open series KXLOWTNYC" })).closest("li");
    fireEvent.mouseEnter(must(within(must(tile)).getByText("?").closest(".lore-anchor")));
    const card = screen.getByRole("tooltip");
    expect(card).toHaveTextContent("The Weather Company");
    expect(card).toHaveTextContent("nws_forecast");
  });

  it("lists markets closing soon first with live countdowns, j/k/Enter opens one", async () => {
    mockKalshi();
    renderExplore({ series: "KXHIGHNY" });
    const table = await screen.findByRole("table", { name: "Markets" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(12);
    expect(rows[0]).toHaveTextContent("KXHIGHNY-26NOV20");
    expect(within(must(rows[0])).getByTestId("countdown")).toHaveTextContent("9h 55m");
    expect(rows[0]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "k" });
    const selected = within(table).getAllByRole("row").slice(1).findIndex((r) => r.getAttribute("aria-selected") === "true");
    expect(selected).toBe(1);
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "ticker" } });
    fireEvent.keyDown(window, { key: "Enter" });
    expect(useNav.getState().params["market"]).toMatch(/^KXHIGHNY-/);
  });

  it("market rules card: settlement, YES meaning, ET close, predictor note", async () => {
    mockKalshi();
    renderExplore({ series: "KXHIGHNY" });
    const table = await screen.findByRole("table", { name: "Markets" });
    const row = must(within(table).getByText(NY_MARKET).closest("tr"));
    fireEvent.mouseEnter(must(within(row).getByText("?").closest(".lore-anchor")));
    const card = await screen.findByRole("tooltip");
    expect(card).toHaveTextContent("Settles onThe Weather Company");
    expect(card).toHaveTextContent("Result in 62 to 63");
    expect(card).toHaveTextContent("Nov 22, 1:00 AM EST");
    expect(card).toHaveTextContent("maximum temperature recorded at New York City (CLINYC)");
    expect(card).toHaveTextContent("Predictor: Feed nws_forecast");
    expect(card).toHaveTextContent("last fair price");
  });

  it("market page: YES and NO panels with best prices and 5 levels each side", async () => {
    mockKalshi();
    renderExplore({ series: "KXHIGHNY", market: NY_MARKET });
    const yes = await screen.findByRole("region", { name: "YES side" });
    const no = screen.getByRole("region", { name: "NO side" });
    const book = parseOrderbookResponse(NY_MARKET, nyBook);
    const pct = (p: number) => formatPrice(Math.round(p * 10_000) / 10_000);
    expect(within(yes).getByTestId("yes-sell")).toHaveTextContent(pct(must(book.yes[0]).price));
    expect(within(yes).getByTestId("yes-buy")).toHaveTextContent(pct(1 - must(book.no[0]).price));
    expect(within(no).getByTestId("no-sell")).toHaveTextContent(pct(must(book.no[0]).price));
    expect(within(no).getByTestId("no-buy")).toHaveTextContent(pct(1 - must(book.yes[0]).price));
    const ladder = within(yes).getByRole("table", { name: "Sell YES (bids, best first)" });
    expect(within(ladder).getAllByRole("row")).toHaveLength(1 + 5);
    expect(screen.getByText(/Collecting top of book/)).toBeInTheDocument();
  });

  it("start a strategy copies the command through main", async () => {
    const api = mockKalshi();
    renderExplore({ series: "KXHIGHNY" });
    expect(screen.getByText("uv run nem strategy add PORTFOLIO NAME --series KXHIGHNY")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start a strategy here" }));
    expect(await screen.findByText(/Copied\./)).toBeInTheDocument();
    expect(api.copyStrategyCommand).toHaveBeenCalledWith("KXHIGHNY");
  });

  it("renders errors as states (rate limited, offline)", async () => {
    mockKalshi({
      markets: () => Promise.resolve({ ok: false, error: { kind: "rate_limited", message: "HTTP 429", status: 429 } }),
    });
    renderExplore({ series: "KXHIGHNY" });
    expect(await screen.findByRole("alert", { name: "Rate limited" })).toHaveTextContent("HTTP 429");

    act(() => useNav.setState({ params: { series: "KXHIGHNY", market: NY_MARKET } }));
    mockKalshi({ market: () => Promise.resolve({ ok: false, error: { kind: "offline", message: "fetch failed" } }) });
    renderExplore({ series: "KXHIGHNY", market: NY_MARKET });
    expect(await screen.findByRole("alert", { name: "Kalshi unreachable" })).toBeInTheDocument();
  });

  it("searches any series", async () => {
    vi.useRealTimers();
    mockKalshi();
    renderExplore({ tab: "search" });
    fireEvent.change(screen.getByLabelText("Search any series"), { target: { value: "KXHIGHNY" } });
    const results = await screen.findByRole("table", { name: "Search results" });
    fireEvent.click(within(results).getByText("KXHIGHNY"));
    expect(useNav.getState().params["series"]).toBe("KXHIGHNY");
  });
});
