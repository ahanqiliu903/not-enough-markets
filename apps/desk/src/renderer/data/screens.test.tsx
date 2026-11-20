import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import CompareScreen from "../screens/compare";
import PortfoliosScreen from "../screens/portfolios";
import StrategyScreen from "../screens/strategy";
import TradesScreen from "../screens/trades";
import { useNav } from "../state/nav";
import { useCompare } from "./compare";
import { alignByTime, alignByTrade, drawdownSeries } from "./series";
import { fakeApi, MISSING_COLUMN, snapshot, STRATEGIES, SUMMARY, type FakeOptions } from "./testData";

function mount(ui: ReactNode, o: FakeOptions = {}) {
  const fake = fakeApi(o);
  window.nem = fake.api;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return fake;
}

afterEach(() => {
  delete window.nem;
  act(() => {
    useCompare.getState().clear();
    useNav.setState({ screen: "portfolios", params: {}, history: [] });
  });
});

describe("Portfolios", () => {
  it("with no source says why and offers sample data", async () => {
    const fake = mount(<PortfoliosScreen />, { snapshot: { state: "none" } });
    const empty = await screen.findByRole("status", { name: "No portfolios yet" });
    expect(empty).toHaveTextContent("uv run nem report");
    expect(empty).toHaveTextContent("make demo");
    fireEvent.click(await screen.findByRole("button", { name: "Load sample data" }));
    await waitFor(() => expect(fake.calls.addSample).toBe(1));
  });

  it("shows the fixture card with equity, P&L vs cash and three verdict badges", async () => {
    mount(<PortfoliosScreen />);
    const card = await screen.findByRole("region", { name: "Portfolio fixture" });
    expect(card).toHaveTextContent("$1059.14");
    expect(card).toHaveTextContent("+$58.95");
    expect(card).toHaveTextContent("P&L vs cash @ 4.00% APY");
    const tiers = [...card.querySelectorAll("[data-tier]")].map((e) => e.textContent);
    expect(tiers).toEqual(["EDGE", "NO EDGE", "UNDECIDED+"]);
    // The fixture's as_of is weeks old: the stale banner says so.
    expect(screen.getByRole("alert")).toHaveTextContent("STALE");
  });

  it("opens a strategy on row click and adds to compare with c", async () => {
    mount(<PortfoliosScreen />);
    const row = (await screen.findByText("eth_no_edge")).closest("tr");
    expect(row).not.toBeNull();
    fireEvent.keyDown(window, { key: "c" });
    expect(useCompare.getState().ids).toEqual(["fixture/btc_edge"]);
    if (row !== null) fireEvent.click(row);
    expect(useNav.getState()).toMatchObject({ screen: "strategy", params: { portfolio: "fixture", strategy: "eth_no_edge" } });
  });

  it("empty portfolio says there are no strategies yet", async () => {
    const s = snapshot({ portfolios: [{ name: "fixture", summary: { ...SUMMARY, started_at: null, pnl_vs_cash_usd: null }, strategies: [], tradeCount: 0, errors: [] }] });
    mount(<PortfoliosScreen />, { snapshot: { state: "ok", snapshot: s } });
    expect(await screen.findByRole("status", { name: "No strategies yet" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Portfolio fixture" })).toHaveTextContent("P&L vs cash");
  });

  it("a schema error names file, field and expected version", async () => {
    const s = snapshot({ portfolios: [{ name: "fixture", summary: SUMMARY, strategies: null, tradeCount: 394, errors: [MISSING_COLUMN] }] });
    mount(<PortfoliosScreen />, { snapshot: { state: "ok", snapshot: s } });
    const err = await screen.findByRole("alert", { name: "Schema error" });
    expect(err).toHaveTextContent("out/fixture/strategies.csv");
    expect(err).toHaveTextContent("edge_cents");
    expect(err).toHaveTextContent("schema version 1.x");
  });
});

describe("Strategy detail", () => {
  it("shows win rate vs break-even, edge, SPRT progress, calibration and reasons", async () => {
    useNav.setState({ params: { portfolio: "fixture", strategy: "btc_edge" } });
    mount(<StrategyScreen />);
    const wr = await screen.findByRole("region", { name: "Win rate vs break-even" });
    expect(wr).toHaveTextContent("99.41%");
    expect(wr).toHaveTextContent("96.74% .. 99.90%");
    expect(wr).toHaveTextContent("89.45%");
    expect(screen.getByRole("img", { name: "Win rate interval vs break-even" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Edge per contract" })).toHaveTextContent("7.29c .. 10.45c");
    const sprt = screen.getByRole("region", { name: "Sequential test progress" });
    expect(sprt).toHaveTextContent("170 settled of 33 needed to detect a 9.96c edge");
    expect(within(sprt).getByRole("progressbar")).toHaveAttribute("aria-valuemax", "33");
    expect(screen.getByRole("table", { name: "Calibration bins" })).toHaveTextContent("93.5%");
    expect(screen.getByRole("table", { name: "Signal reasons" })).toHaveTextContent("ok: fixed 5");
    expect(await screen.findByText(/2 settled trades; final/)).toBeInTheDocument();
  });

  it("without a selection lists strategies to pick", async () => {
    mount(<StrategyScreen />);
    expect(await screen.findByRole("status", { name: "No strategy selected" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "sol_undecided" }));
    expect(useNav.getState().params).toEqual({ portfolio: "fixture", strategy: "sol_undecided" });
  });
});

describe("Trades blotter", () => {
  // jsdom has no layout; give the scroll container a 600px viewport for the virtualizer.
  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 600 });
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 1200 });
  });
  afterAll(() => {
    Reflect.deleteProperty(HTMLElement.prototype, "clientHeight");
    Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
  });

  it("renders rows, sorts in main and filters", async () => {
    const fake = mount(<TradesScreen />);
    expect(await screen.findByText("KXBTC15M-W0-00")).toBeInTheDocument();
    expect(screen.getByTestId("trade-count")).toHaveTextContent("50 of 394 trades");
    expect(fake.calls.trades[0]?.sort).toEqual({ column: "opened_at", dir: "desc" });
    fireEvent.click(screen.getByRole("columnheader", { name: "P&L" }));
    await waitFor(() => expect(fake.calls.trades.at(-1)?.sort?.column).toBe("pnl_usd"));
    fireEvent.change(screen.getByRole("combobox", { name: "Result" }), { target: { value: "lost" } });
    await waitFor(() => expect(fake.calls.trades.at(-1)?.result).toBe("lost"));
  });

  it("virtualizes 100k rows: only a screenful is in the DOM, pages fetched on demand", async () => {
    const fake = mount(<TradesScreen />, { totalTrades: 100_000 });
    await waitFor(() => expect(screen.getByTestId("trade-count")).toHaveTextContent("100,000 of 394"));
    const rows = screen.getAllByRole("row");
    expect(rows.length).toBeGreaterThan(1);
    expect(rows.length).toBeLessThan(150);
    expect(fake.calls.trades.every((q) => q.limit === 200)).toBe(true);
  });

  it("a blocked trades.csv shows the schema error", async () => {
    mount(<TradesScreen />, { tradesError: [{ kind: "invalid", file: "out/fixture/trades.csv", row: 1, field: "opened_at", message: "row 1: opened_at must match pattern" }] });
    const err = await screen.findByRole("alert", { name: "Schema error" });
    expect(err).toHaveTextContent("opened_at");
  });
});

describe("Compare", () => {
  it("needs two picks, then shows curves and a stats table with badges", async () => {
    mount(<CompareScreen />);
    expect(await screen.findByRole("status", { name: "Pick at least two strategies" })).toBeInTheDocument();
    for (const name of ["btc_edge", "eth_no_edge"]) {
      fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(`^${name}`) }));
    }
    const table = await screen.findByRole("table", { name: "Compare statistics" });
    expect([...table.querySelectorAll("[data-tier]")].map((e) => e.textContent)).toEqual(["EDGE", "NO EDGE"]);
    expect(table).toHaveTextContent("-16.96c");
    expect(await screen.findByRole("img", { name: "Cumulative P&L, compared" })).toBeInTheDocument();
  });

  it("includes retired strategies in the picker", async () => {
    const rows = STRATEGIES.map((s, i) => (i === 1 ? { ...s, status: "retired" as const } : s));
    mount(<CompareScreen />, { snapshot: { state: "ok", snapshot: snapshot({ portfolios: [{ name: "fixture", summary: SUMMARY, strategies: rows, tradeCount: 394, errors: [] }] }) } });
    expect(await screen.findByRole("checkbox", { name: /eth_no_edge fixture, retired/ })).toBeInTheDocument();
  });
});

describe("series helpers", () => {
  const a = { portfolio: "p", strategy: "a", t: [10, 30], pnl: [1, 3] };
  const b = { portfolio: "p", strategy: "b", t: [20], pnl: [-2] };
  it("aligns by trade number from a common 0", () => {
    expect(alignByTrade([a, b])).toEqual({ x: [0, 1, 2], ys: [[0, 1, 3], [0, -2, null]] });
  });
  it("aligns by time from the earliest first settlement, holding values", () => {
    expect(alignByTime([a, b])).toEqual({ x: [9, 10, 20, 30], ys: [[0, 1, 1, 3], [0, 0, -2, -2]] });
  });
  it("drawdown is the distance below the running peak", () => {
    expect(drawdownSeries([1, 3, 2, -1, 4])).toEqual([0, 0, -1, -4, 0]);
  });
});
