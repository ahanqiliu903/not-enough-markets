import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import PortfoliosScreen from "../screens/portfolios";
import StrategyScreen from "../screens/strategy";
import TradesScreen from "../screens/trades";
import { useNav } from "../state/nav";
import { excludedCount, excludedFlag } from "./format";
import { fakeApi, snapshot, STRATEGIES, SUMMARY, trade, type FakeOptions } from "./testData";

/** Trades excluded from statistics (contract 1.1): shown muted with the engine's reason; counts are the engine's. */

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

const WITH_EXCLUDED = snapshot({
  portfolios: [
    {
      name: "fixture",
      summary: { ...SUMMARY, excluded_trades: 2 },
      strategies: STRATEGIES.map((s, i) => (i === 0 ? { ...s, excluded_trades: 2 } : s)),
      tradeCount: 394,
      errors: [],
    },
  ],
});

describe("excluded trades display", () => {
  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 600 });
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 1200 });
  });
  afterAll(() => {
    Reflect.deleteProperty(HTMLElement.prototype, "clientHeight");
    Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
  });

  it("formats the flag and the count", () => {
    expect(excludedFlag({ excluded: true, exclude_reason: "bad fill" })).toBe("excluded: bad fill");
    expect(excludedFlag({ excluded: true, exclude_reason: null })).toBe("excluded: no reason given");
    expect(excludedFlag({ excluded: false, exclude_reason: null })).toBe("");
    expect(excludedFlag({ excluded: null, exclude_reason: null })).toBe("");
    expect(excludedCount(2)).toBe("2");
    expect(excludedCount(null)).toBe("-");
  });

  it("Trades mutes an excluded row and shows its reason", async () => {
    const row = (i: number) => (i === 0 ? { ...trade(i), excluded: true, exclude_reason: "duplicate order" } : trade(i));
    mount(<TradesScreen />, { totalTrades: 2, tradeRow: row });
    expect(await screen.findByText("KXBTC15M-W0-00")).toBeInTheDocument();
    const excluded = screen.getByText("KXBTC15M-W0-00").closest('[role="row"]');
    expect(excluded).toHaveClass("excluded");
    expect(excluded).toHaveTextContent("excluded: duplicate order");
    expect(excluded).toHaveTextContent("-$4.49"); // money still shown
    const counted = screen.getByText("KXBTC15M-W1-00").closest('[role="row"]');
    expect(counted).not.toHaveClass("excluded");
    expect(counted).toHaveTextContent("counted");
  });

  it("Hide excluded asks main to filter them out", async () => {
    const fake = mount(<TradesScreen />, { totalTrades: 1 });
    expect(await screen.findByText("KXBTC15M-W0-00")).toBeInTheDocument();
    expect(fake.calls.trades.at(-1)?.hideExcluded).toBeUndefined();
    await userEvent.click(screen.getByRole("checkbox", { name: "Hide excluded" }));
    await waitFor(() => expect(fake.calls.trades.at(-1)?.hideExcluded).toBe(true));
  });

  it("Portfolios and Strategy show the engine's excluded count", async () => {
    mount(<PortfoliosScreen />, { snapshot: { state: "ok", snapshot: WITH_EXCLUDED } });
    const card = await screen.findByRole("region", { name: "Portfolio fixture" });
    expect(card).toHaveTextContent("Excluded trades2");
    expect(card.querySelector('[data-strategy="btc_edge"]')).toHaveTextContent("170 (+2 excl.)");
    expect(card.querySelector('[data-strategy="eth_no_edge"]')).not.toHaveTextContent("excl.");
    cleanup();
    act(() => useNav.setState({ screen: "strategy", params: { portfolio: "fixture", strategy: "btc_edge" }, history: [] }));
    mount(<StrategyScreen />, { snapshot: { state: "ok", snapshot: WITH_EXCLUDED } });
    const stats = await screen.findByRole("region", { name: "Other statistics" });
    expect(stats).toHaveTextContent("Excluded from statistics2");
    expect(stats).toHaveTextContent("2 trade(s) excluded (nem trade exclude): left out of statistics, P&L and equity");
  });
});
