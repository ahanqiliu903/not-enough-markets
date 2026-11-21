import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { DataError, Snapshot, StatusReport } from "@shared/data";
import { fakeApi, snapshot } from "../../data/testData";
import { useNav } from "../../state/nav";
import StatusScreen from ".";

/** contract/fixtures/basic/status.json */
const STATUS: StatusReport = {
  schema_version: "1.0",
  as_of: "2026-10-02T18:35:00Z",
  healthy: true,
  processes: [
    { name: "runner", state: "ok", last_heartbeat_at: "2026-10-02T18:34:00Z", detail: "ok", expected: true },
    { name: "reporter", state: "ok", last_heartbeat_at: "2026-10-02T18:34:00Z", detail: "ok", expected: true },
  ],
  halts: [{ scope: "fixture/eth_no_edge", reason: "fixture: verdict no_edge", since: "2026-10-02T18:30:00Z" }],
  strategies: [
    { portfolio: "fixture", strategy: "btc_edge", series: "KXBTC15M", status: "active", halted_by: null, open: 0, settled: 170, last_signal_at: "2026-10-02T18:21:00Z" },
    { portfolio: "fixture", strategy: "eth_no_edge", series: "KXETH15M", status: "active", halted_by: "fixture/eth_no_edge", open: 0, settled: 54, last_signal_at: "2026-10-01T17:06:00Z" },
  ],
};

function mount(s: Snapshot) {
  window.nem = fakeApi({ snapshot: { state: "ok", snapshot: s } }).api;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <StatusScreen />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  delete window.nem;
  act(() => useNav.setState({ screen: "portfolios", params: {}, history: [] }));
});

describe("Feeds and Status", () => {
  it("shows heartbeats, halts and per-strategy activity from status.json", async () => {
    mount(snapshot({ status: STATUS }));
    const procs = await screen.findByRole("table", { name: "Processes" });
    const runner = within(procs).getAllByRole("row")[1];
    expect(runner).toHaveTextContent("runner");
    expect(runner).toHaveTextContent("OK");
    expect(runner).toHaveTextContent("2026-10-02 18:34:00Z");
    expect(runner).toHaveTextContent("1m before report");
    expect(screen.getByText("HEALTHY")).toBeInTheDocument();

    const halts = screen.getByRole("table", { name: "Halts" });
    expect(halts).toHaveTextContent("fixture/eth_no_edge");
    expect(halts).toHaveTextContent("fixture: verdict no_edge");
    expect(halts).toHaveTextContent("2026-10-02 18:30:00Z");

    const act_ = screen.getByRole("table", { name: "Strategy activity" });
    const eth = within(act_).getByText("eth_no_edge").closest("tr");
    expect(eth).toHaveTextContent("fixture/eth_no_edge");
    expect(eth).toHaveTextContent("54");
    if (eth !== null) fireEvent.click(eth);
    expect(useNav.getState()).toMatchObject({ screen: "strategy", params: { portfolio: "fixture", strategy: "eth_no_edge" } });

    // Feed freshness is not in contract 1.0: said, not invented.
    expect(screen.getByRole("region", { name: "Feeds" })).toHaveTextContent("not in status.json");
    expect(screen.getByRole("alert")).toHaveTextContent("STALE");
  });

  it("shows stale and never processes and an unhealthy engine as the engine said", async () => {
    const st: StatusReport = {
      ...STATUS,
      healthy: false,
      processes: [
        { name: "runner", state: "stale", last_heartbeat_at: "2026-10-02T17:00:00Z", detail: null, expected: true },
        { name: "recorder", state: "never", last_heartbeat_at: null, detail: null, expected: false },
      ],
      halts: [],
    };
    mount(snapshot({ status: st }));
    const procs = await screen.findByRole("table", { name: "Processes" });
    expect(procs.querySelector('[data-state="stale"]')).toHaveTextContent("STALE");
    expect(procs.querySelector('[data-state="never"]')).toHaveTextContent("NEVER");
    expect(procs).toHaveTextContent("1h 35m before report");
    expect(screen.getByText("UNHEALTHY")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Halts" })).toHaveTextContent("No halts.");
  });

  it("without status.json says how to make it", async () => {
    mount(snapshot({ status: null }));
    expect(await screen.findByRole("status", { name: "No engine status" })).toHaveTextContent("nem report");
  });

  it("a status.json schema error names the file", async () => {
    const err: DataError = { kind: "version", file: "status.json", field: "schema_version", message: "major 2", expected: "1.x", found: "2.0" };
    mount(snapshot({ status: null, errors: [err] }));
    const alert = await screen.findByRole("alert", { name: "Schema error" });
    expect(alert).toHaveTextContent("status.json");
    expect(alert).toHaveTextContent("2.0");
    expect(screen.queryByRole("status", { name: "No engine status" })).toBeNull();
  });
});
