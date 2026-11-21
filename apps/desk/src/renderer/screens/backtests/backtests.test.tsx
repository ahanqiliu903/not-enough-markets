import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it } from "vitest";
import type { BacktestRun, DataError, Snapshot } from "@shared/data";
import { fakeApi, snapshot, STRATEGIES } from "../../data/testData";
import { useNav } from "../../state/nav";
import BacktestsScreen from ".";
import { buildHeatmap, heatBucket, intervalsDisjoint, windowSpans } from "./model";
import { RUN } from "./testRun";

function mount(ui: ReactNode, s: Snapshot) {
  window.nem = fakeApi({ snapshot: { state: "ok", snapshot: s } }).api;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  delete window.nem;
  act(() => useNav.setState({ screen: "portfolios", params: {}, history: [] }));
});

describe("backtest model", () => {
  it("lays a 2-axis grid out by variant index: rows = axis 0, columns = axis 1", () => {
    const hm = buildHeatmap(RUN);
    expect(hm?.rowAxis?.path).toBe("signal.threshold");
    expect(hm?.colAxis.path).toBe("gates.0.value");
    expect(hm?.cells.length).toBe(4);
    expect(hm?.cells[2]?.[1]?.variant?.name).toBe("v05");
    expect(hm?.cells[0]?.[0]).toMatchObject({ chosen: true, edge: 3.07, n: 119, bucket: "up-3" });
    expect(hm?.cells[2]?.[0]?.bucket).toBe("down-2");
    expect(hm?.cells.flat().filter((c) => c.chosen)).toHaveLength(1);
  });

  it("mutes cells under min_trades and leaves them out of the color scale", () => {
    const run: BacktestRun = { ...RUN, min_trades: 100 };
    const hm = buildHeatmap(run);
    const muted = hm?.cells.flat().filter((c) => c.muted).map((c) => c.variant?.name);
    expect(muted).toEqual(["v04", "v05", "v06", "v07"]);
    expect(hm?.cells[3]?.[0]?.bucket).toBe("none");
  });

  it("one axis is one row; three axes have no heatmap", () => {
    const one: BacktestRun = {
      ...RUN,
      grid: [{ path: "signal.threshold", values: [0.86, 0.88] }],
      variants: RUN.variants.slice(0, 2).map((v, i) => ({ ...v, index: [i] })),
    };
    const hm = buildHeatmap(one);
    expect(hm?.rowAxis).toBeNull();
    expect(hm?.cells).toHaveLength(1);
    expect(hm?.cells[0]?.map((c) => c.variant?.name)).toEqual(["v00", "v01"]);
    expect(buildHeatmap({ ...RUN, grid: [...RUN.grid, { path: "x", values: [1] }] })).toBeNull();
  });

  it("buckets by sign and size; null and zero are neutral", () => {
    expect(heatBucket(null, 5)).toBe("none");
    expect(heatBucket(0, 5)).toBe("zero");
    expect(heatBucket(1, 6)).toBe("up-1");
    expect(heatBucket(-3, 6)).toBe("down-2");
    expect(heatBucket(6, 6)).toBe("up-3");
  });

  it("flags only non-overlapping intervals", () => {
    expect(intervalsDisjoint({ lo: 1, hi: 2 }, { lo: 3, hi: 4 })).toBe(true);
    expect(intervalsDisjoint({ lo: 1, hi: 3 }, { lo: 2, hi: 4 })).toBe(false);
    expect(intervalsDisjoint({ lo: null, hi: 3 }, { lo: 5, hi: 6 })).toBe(false);
  });

  it("places train before the held-out test window", () => {
    const w = windowSpans(RUN);
    expect(w?.train[0]).toBe(0);
    expect(w?.test[1]).toBe(1);
    expect(w?.train[1]).toBeCloseTo(w?.test[0] ?? -1);
  });
});

describe("Backtests screen", () => {
  it("lists runs with portfolio, strategy, series, created and status, and opens one", async () => {
    mount(<BacktestsScreen />, snapshot({ backtests: [RUN] }));
    const table = await screen.findByRole("table", { name: "Backtest runs" });
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("fixture");
    expect(row).toHaveTextContent("sol_undecided");
    expect(row).toHaveTextContent("KXSOL15M");
    expect(row).toHaveTextContent("2026-10-02 18:35:00Z");
    expect(row).toHaveTextContent("SUGGESTED");
    fireEvent.keyDown(window, { key: "Enter" });
    expect(useNav.getState()).toMatchObject({ screen: "backtests", params: { run: RUN.run_id } });
  });

  it("with no runs says how to make one; a broken run file is a schema error", async () => {
    const err: DataError = { kind: "invalid", file: "out/backtests/x.json", field: "/variants", message: "must be array" };
    mount(<BacktestsScreen />, snapshot({ errors: [err] }));
    expect(await screen.findByRole("status", { name: "No backtests" })).toHaveTextContent("nem suggest");
    expect(screen.getByRole("alert", { name: "Schema error" })).toHaveTextContent("out/backtests/x.json");
  });

  it("detail: grid, windows, chosen variant, train vs test, fill estimate, heatmap", async () => {
    act(() => useNav.setState({ screen: "backtests", params: { run: RUN.run_id } }));
    mount(<BacktestsScreen />, snapshot({ backtests: [RUN] }));
    expect(await screen.findByRole("region", { name: "Chosen variant" })).toHaveTextContent("signal.threshold=0.86");
    expect(screen.getByRole("region", { name: "Chosen variant" })).toHaveTextContent("fill estimate: not estimated");
    expect(screen.getByRole("table", { name: "Windows" })).toHaveTextContent("2026-10-02 05:45:00Z");
    expect(screen.getByRole("table", { name: "Grid parameters" })).toHaveTextContent("gates.0.value");

    const tt = screen.getByRole("table", { name: "Train vs test metrics" });
    expect(tt).toHaveTextContent("90.76%");
    expect(tt).toHaveTextContent("94.12%");
    expect(screen.getByRole("region", { name: "Train vs test" })).toHaveTextContent("chosen variant only, by design");

    const hm = screen.getByRole("table", { name: "Sweep heatmap" });
    const cells = hm.querySelectorAll("td.heat-cell");
    expect(cells).toHaveLength(8);
    const chosen = hm.querySelectorAll("td.heat-chosen");
    expect(chosen).toHaveLength(1);
    expect(chosen[0]).toHaveTextContent("3.1c");
    expect(chosen[0]).toHaveTextContent("n 119");
    expect(chosen[0]).toHaveTextContent("CHOSEN");
    expect(screen.getByRole("table", { name: "Variants" })).toHaveTextContent("not run (by design)");
  });

  it("detail without a chosen variant says so instead of showing empty test metrics", async () => {
    const run: BacktestRun = { ...RUN, status: "insufficient", chosen: null, score_cents: null, variants: RUN.variants.map((v) => ({ ...v, test: null })) };
    act(() => useNav.setState({ screen: "backtests", params: { run: run.run_id } }));
    mount(<BacktestsScreen />, snapshot({ backtests: [run] }));
    expect(await screen.findByRole("region", { name: "Chosen variant" })).toHaveTextContent("No variant chosen (insufficient)");
    expect(screen.getByRole("table", { name: "Train vs test metrics" })).toHaveTextContent("no variant chosen");
    expect(document.querySelectorAll("td.heat-chosen")).toHaveLength(0);
  });

  it("vs paper: backtest train/test next to the paper row, live not implemented", async () => {
    act(() => useNav.setState({ screen: "backtests", params: { run: RUN.run_id } }));
    mount(<BacktestsScreen />, snapshot({ backtests: [RUN] }));
    const panel = await screen.findByRole("region", { name: "Backtest vs paper vs live" });
    const t = within(panel).getByRole("table", { name: "Backtest vs paper metrics" });
    // Paper values are strategies.csv's sol_undecided row, unchanged.
    expect(t).toHaveTextContent("91.76%");
    expect(t).toHaveTextContent("2.36c");
    expect(t).toHaveTextContent("live: not implemented");
    expect(panel).toHaveTextContent("live: not implemented");
    expect(within(panel).queryByRole("alert")).toBeNull();
    expect(panel.querySelector("[data-tier]")).toHaveTextContent("UNDECIDED+");
  });

  it("vs paper flags a large divergence and handles a missing paper row", async () => {
    const paper = STRATEGIES.map((r) => (r.strategy === "sol_undecided" ? { ...r, edge_lo_cents: -30, edge_hi_cents: -10 } : r));
    act(() => useNav.setState({ screen: "backtests", params: { run: RUN.run_id } }));
    const base = snapshot({ backtests: [RUN] });
    const first = base.portfolios[0];
    if (first === undefined) throw new Error("fixture portfolio missing");
    mount(<BacktestsScreen />, { ...base, portfolios: [{ ...first, strategies: paper }] });
    const panel = await screen.findByRole("region", { name: "Backtest vs paper vs live" });
    expect(within(panel).getByRole("alert")).toHaveTextContent("DIVERGES");
  });

  it("vs paper without a paper row says it is not in this source", async () => {
    act(() => useNav.setState({ screen: "backtests", params: { run: RUN.run_id } }));
    mount(<BacktestsScreen />, snapshot({ backtests: [{ ...RUN, strategy: "gone" }], }));
    const panel = await screen.findByRole("region", { name: "Backtest vs paper vs live" });
    expect(panel).toHaveTextContent("fixture/gone is not in this source's strategies.csv");
  });
});
