import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import PortfoliosScreen from "../screens/portfolios";
import StrategyScreen from "../screens/strategy";
import TradesScreen from "../screens/trades";
import { useNav } from "../state/nav";
import { pendingPnl, pendingSummary, provisionalClass, provisionalText } from "./format";
import { fakeApi, snapshot, STRATEGIES, SUMMARY, trade, type FakeOptions } from "./testData";

/** Provisional results of open trades (contract 1.1): engine values, labelled "not counted". */

function mount(ui: ReactNode, o: FakeOptions = {}) {
  const fake = fakeApi(o);
  window.nem = fake.api;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return fake;
}

afterEach(() => {
  delete window.nem;
  act(() => useNav.setState({ screen: "portfolios", params: {}, history: [] }));
});

const PENDING = { pending_locked_pnl_usd: 1.98, pending_likely_pnl_usd: 3.20, pending_locked: 1, pending_likely: 4, pending_conflict: 1 };

const WITH_PENDING = snapshot({
  portfolios: [
    {
      name: "fixture",
      summary: { ...SUMMARY, open_positions: 8, ...PENDING },
      strategies: STRATEGIES.map((s, i) => (i === 0 ? { ...s, open: 8, ...PENDING } : s)),
      tradeCount: 394,
      errors: [],
    },
  ],
});

const pv = (result: string | null, basis: string | null, extreme: number | null = null) => ({
  provisional_result: result,
  provisional_basis: basis,
  observed_extreme_f: extreme,
});

describe("pending (provisional) display", () => {
  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 600 });
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 1200 });
  });
  afterAll(() => {
    Reflect.deleteProperty(HTMLElement.prototype, "clientHeight");
    Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
  });

  it("formats a trade's provisional result", () => {
    expect(provisionalText(pv("locked_win", "observed", 56))).toBe("locked win (obs 56°F)");
    expect(provisionalText(pv("likely_loss", "price"))).toBe("likely loss (price)");
    expect(provisionalText(pv("likely_win", "both", 62))).toBe("likely win (obs 62°F + price)");
    expect(provisionalText(pv("conflict", "both", 60))).toBe("conflict (obs 60°F vs price)");
    expect(provisionalText(pv("unknown", null, 50))).toBe("");
    expect(provisionalText(pv(null, null))).toBe("");
    expect(provisionalText(pv("settled_soon", null))).toBe("settled soon"); // lenient
    expect(provisionalClass("locked_win")).toBe("up");
    expect(provisionalClass("likely_loss")).toBe("down");
    expect(provisionalClass("conflict")).toBe("");
  });

  it("formats pending totals", () => {
    expect(pendingPnl(1.98, 1)).toBe("+$1.98 (1)");
    expect(pendingPnl(0, 0)).toBe("0");
    expect(pendingPnl(null, null)).toBe("-");
    expect(pendingSummary(PENDING)).toBe("locked +$1.98 (1); likely +$3.20 (4); 1 conflict");
    expect(pendingSummary({ ...PENDING, pending_locked: null })).toBe("-");
  });

  it("Portfolios shows pending locked and likely P&L as not counted", async () => {
    mount(<PortfoliosScreen />, { snapshot: { state: "ok", snapshot: WITH_PENDING } });
    const card = await screen.findByRole("region", { name: "Portfolio fixture" });
    expect(card).toHaveTextContent("Pending locked (not counted)+$1.98 (1)");
    expect(card).toHaveTextContent("Pending likely (not counted)+$3.20 (4); 1 conflict");
    expect(card.querySelector('[data-strategy="btc_edge"]')).toHaveTextContent("locked +$1.98 (1); likely +$3.20 (4); 1 conflict");
    cleanup();
    act(() => useNav.setState({ screen: "strategy", params: { portfolio: "fixture", strategy: "btc_edge" }, history: [] }));
    mount(<StrategyScreen />, { snapshot: { state: "ok", snapshot: WITH_PENDING } });
    const stats = await screen.findByRole("region", { name: "Other statistics" });
    expect(stats).toHaveTextContent("Pending locked (not counted)+$1.98 (1)");
    expect(stats).toHaveTextContent("Pending likely (not counted)+$3.20 (4)");
    expect(stats).toHaveTextContent("Pending conflicts1");
    expect(stats).toHaveTextContent("not counted: Kalshi settles");
  });

  it("Trades shows each open trade's pending result", async () => {
    const open = (i: number) => ({
      ...trade(i),
      settled_at: null,
      result: "open" as const,
      pnl_usd: null,
      ...pv("locked_win", "observed", 56),
      provisional_pnl_usd: 1.98,
    });
    mount(<TradesScreen />, { totalTrades: 1, tradeRow: open });
    expect(await screen.findByText("KXBTC15M-W0-00")).toBeInTheDocument();
    const row = screen.getByText("KXBTC15M-W0-00").closest('[role="row"]');
    expect(row).toHaveTextContent("locked win (obs 56°F) +$1.98");
    expect(screen.getByText(/Pending \(open trades\)/)).toHaveTextContent("not counted: Kalshi settles");
  });
});
