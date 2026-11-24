import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import PortfoliosScreen from "../screens/portfolios";
import { useNav } from "../state/nav";
import { EQUITY_LABEL } from "./format";
import { fakeApi, type FakeOptions } from "./testData";

/** Portfolios: the engine's value over time (equity.csv, contract 1.1), displayed only. */

function mount(o: FakeOptions = {}) {
  window.nem = fakeApi(o).api;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PortfoliosScreen />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  delete window.nem;
  act(() => useNav.setState({ screen: "portfolios", params: {}, history: [] }));
});

const CURVE = {
  portfolio: "fixture",
  t: [1790812800, 1790816400, 1790818200],
  value: [1000, 1000.25, 1003.5],
  book: [1000, 1000.5, 1004],
  lastUnmarked: 2,
};

describe("portfolio value chart", () => {
  it("draws value and at-cost series with the label and last values", async () => {
    mount({ equity: { ok: true, curve: CURVE } });
    const panel = await screen.findByRole("group", { name: "Value over time: fixture" });
    expect(await screen.findByRole("img", { name: "Portfolio value over time: fixture" })).toBeInTheDocument();
    expect(panel).toHaveTextContent("Value (marked)");
    expect(panel).toHaveTextContent("At cost (realized only)");
    expect(panel).toHaveTextContent("Starting balance");
    expect(panel).toHaveTextContent(EQUITY_LABEL);
    expect(panel).toHaveTextContent("value $1003.50, at cost $1004.00 (2 open unmarked, at cost); 3 points");
  });

  it("an older source without equity.csv hides the chart with a note", async () => {
    mount();
    const panel = await screen.findByRole("group", { name: "Value over time: fixture" });
    expect(await screen.findByText(/No value history for fixture/)).toBeInTheDocument();
    expect(panel).toHaveTextContent("Chart hidden");
    expect(screen.queryByRole("img", { name: /Portfolio value over time/ })).toBeNull();
  });

  it("a broken equity.csv shows its schema error", async () => {
    mount({ equity: { ok: false, errors: [{ kind: "invalid", file: "out/fixture/equity.csv", row: 1, field: "ts", message: "row 1: ts must match pattern" }] } });
    expect(await screen.findByRole("alert", { name: "Schema error" })).toHaveTextContent("ts");
  });
});
