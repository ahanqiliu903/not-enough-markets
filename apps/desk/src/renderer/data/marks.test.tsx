import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import PortfoliosScreen from "../screens/portfolios";
import StrategyScreen from "../screens/strategy";
import TradesScreen from "../screens/trades";
import { useNav } from "../state/nav";
import { noBidContracts, noBidFlag, unrealized } from "./format";
import { fakeApi, snapshot, STRATEGIES, SUMMARY, trade, type FakeOptions } from "./testData";

/** Engine mark-to-market values (contract 1.1) are displayed, never recomputed. */

function mount(ui: ReactNode, o: FakeOptions = {}) {
  window.nem = fakeApi(o).api;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  delete window.nem;
  act(() => useNav.setState({ screen: "portfolios", params: {}, history: [] }));
});

const MARKED = snapshot({
  portfolios: [
    {
      name: "fixture",
      summary: { ...SUMMARY, open_positions: 2, open_cost_usd: 9.04, open_value_usd: 4.38, unrealized_pnl_usd: -0.14, open_unmarked: 1 },
      strategies: STRATEGIES.map((s, i) =>
        i === 0 ? { ...s, open: 2, open_value_usd: 4.38, unrealized_pnl_usd: -0.14, open_unmarked: 1 } : s,
      ),
      tradeCount: 394,
      errors: [],
    },
  ],
});

describe("mark-to-market display", () => {
  // jsdom has no layout; give the scroll container a viewport for the virtualizer.
  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 600 });
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 1200 });
  });
  afterAll(() => {
    Reflect.deleteProperty(HTMLElement.prototype, "clientHeight");
    Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
  });

  it("formats unrealized P&L with the unmarked count", () => {
    expect(unrealized(-0.14, 1)).toBe("-$0.14 (1 unmarked)");
    expect(unrealized(0.3, 0)).toBe("+$0.30");
    expect(unrealized(null, 2)).toBe("-");
  });

  it("formats contracts without bids", () => {
    expect(unrealized(-4.52, 0, 5)).toBe("-$4.52 (5 no bids at $0)");
    expect(unrealized(-1.2, 1, 3)).toBe("-$1.20 (1 unmarked; 3 no bids at $0)");
    expect(noBidFlag(2, 5)).toBe("no bids: 3 at $0 (pending loss)");
    expect(noBidFlag(5, 5)).toBe("");
    expect(noBidFlag(null, 5)).toBe("");
    expect(noBidContracts(10)).toBe("10 (pending loss)");
    expect(noBidContracts(0)).toBe("0");
    expect(noBidContracts(null)).toBe("-");
  });

  it("Portfolios and Strategy show contracts with no bids", async () => {
    const noBid = snapshot({
      portfolios: [
        {
          name: "fixture",
          summary: { ...SUMMARY, open_positions: 1, open_cost_usd: 4.52, open_value_usd: 0, unrealized_pnl_usd: -4.52, open_unmarked: 0, open_no_bid_contracts: 5 },
          strategies: STRATEGIES.map((s, i) =>
            i === 0 ? { ...s, open: 1, open_value_usd: 0, unrealized_pnl_usd: -4.52, open_unmarked: 0, open_no_bid_contracts: 5 } : s,
          ),
          tradeCount: 394,
          errors: [],
        },
      ],
    });
    mount(<PortfoliosScreen />, { snapshot: { state: "ok", snapshot: noBid } });
    const card = await screen.findByRole("region", { name: "Portfolio fixture" });
    expect(card).toHaveTextContent("No bids5 (pending loss)");
    expect(card.querySelector('[data-strategy="btc_edge"]')).toHaveTextContent("-$4.52 (5 no bids at $0)");
    cleanup();
    act(() => useNav.setState({ screen: "strategy", params: { portfolio: "fixture", strategy: "btc_edge" }, history: [] }));
    mount(<StrategyScreen />, { snapshot: { state: "ok", snapshot: noBid } });
    const stats = await screen.findByRole("region", { name: "Other statistics" });
    expect(stats).toHaveTextContent("Contracts with no bids5 (pending loss)");
    expect(stats).toHaveTextContent("Unrealized P&L-$4.52");
  });

  it("Portfolios shows open value and unrealized P&L from the engine", async () => {
    mount(<PortfoliosScreen />, { snapshot: { state: "ok", snapshot: MARKED } });
    const card = await screen.findByRole("region", { name: "Portfolio fixture" });
    expect(card).toHaveTextContent("Open value$4.38");
    expect(card).toHaveTextContent("Unrealized P&L-$0.14 (1 unmarked)");
    const row = card.querySelector('[data-strategy="btc_edge"]');
    expect(row).toHaveTextContent("-$0.14 (1 unmarked)");
  });

  it("Strategy detail lists open value and unrealized P&L", async () => {
    act(() => useNav.setState({ screen: "strategy", params: { portfolio: "fixture", strategy: "btc_edge" }, history: [] }));
    mount(<StrategyScreen />, { snapshot: { state: "ok", snapshot: MARKED } });
    const stats = await screen.findByRole("region", { name: "Other statistics" });
    expect(stats).toHaveTextContent("Open value$4.38");
    expect(stats).toHaveTextContent("liquidation value at best bids, after exit fee");
  });

  it("Trades shows each open trade's mark", async () => {
    const open = (i: number) => ({
      ...trade(i),
      settled_at: null,
      result: "open" as const,
      pnl_usd: null,
      mark_bid_cents: 88,
      mark_mid_cents: 89,
      mark_contracts: 5,
      mark_value_usd: 4.38,
      unrealized_pnl_usd: -0.14,
      marked_at: "2026-10-01T00:05:00Z",
    });
    mount(<TradesScreen />, { totalTrades: 1, tradeRow: open });
    expect(await screen.findByText("KXBTC15M-W0-00")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Unrealized" })).toBeInTheDocument();
    const body = screen.getByText("KXBTC15M-W0-00").closest('[role="row"]');
    expect(body).toHaveTextContent("-$0.14");
    expect(body).toHaveTextContent("$4.38");
    expect(body).toHaveTextContent("2026-10-01 00:05:00Z");
    expect(body).not.toHaveTextContent("no bids");
  });

  it("Trades flags contracts the bids don't cover", async () => {
    const partial = (i: number) => ({
      ...trade(i),
      contracts: 5,
      settled_at: null,
      result: "open" as const,
      pnl_usd: null,
      mark_bid_cents: 88,
      mark_mid_cents: 89,
      mark_contracts: 2,
      mark_value_usd: 1.72,
      unrealized_pnl_usd: -2.8,
      marked_at: "2026-10-01T00:05:00Z",
    });
    mount(<TradesScreen />, { totalTrades: 1, tradeRow: partial });
    expect(await screen.findByText("KXBTC15M-W0-00")).toBeInTheDocument();
    const body = screen.getByText("KXBTC15M-W0-00").closest('[role="row"]');
    expect(body).toHaveTextContent("2 · no bids: 3 at $0 (pending loss)");
    expect(body).toHaveTextContent("-$2.80");
  });

  it("Trades flags an empty bid side as all contracts at $0", async () => {
    const empty = (i: number) => ({
      ...trade(i),
      contracts: 5,
      settled_at: null,
      result: "open" as const,
      pnl_usd: null,
      mark_bid_cents: null,
      mark_mid_cents: null,
      mark_contracts: 0,
      mark_value_usd: 0,
      unrealized_pnl_usd: -4.52,
      marked_at: "2026-10-01T00:05:00Z",
    });
    mount(<TradesScreen />, { totalTrades: 1, tradeRow: empty });
    expect(await screen.findByText("KXBTC15M-W0-00")).toBeInTheDocument();
    const body = screen.getByText("KXBTC15M-W0-00").closest('[role="row"]');
    expect(body).toHaveTextContent("0 · no bids: 5 at $0 (pending loss)");
    expect(body).toHaveTextContent("-$4.52");
  });
});
