import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it } from "vitest";
import type { DbApi, DbError, DbInfo, DbMarket, DbResult, DbTopPoint } from "@shared/db";
import type { NemApi } from "@shared/ipc";
import type { VpsApi, VpsState } from "@shared/vps";
import { useNav } from "../../state/nav";
import SettingsScreen from "../settings";
import { depthCurves } from "./DepthChart";
import MarketScreen from "./index";
import { alignSeries, nearestPoint } from "./TopChart";

const T0 = Date.UTC(2026, 9, 1) / 1000;

const INFO: DbInfo = {
  path: "/fixtures/basic/nem.db",
  schemaVersion: 3,
  sizeBytes: 2_000_000,
  series: [
    { series: "KXBTC15M", snapshots: 30, markets: 2, firstTs: "2026-10-01T00:00:00.000000+00:00", lastTs: "2026-10-01T00:29:00.000000+00:00" },
    { series: "KXETH15M", snapshots: 15, markets: 1, firstTs: "2026-10-01T00:00:00.000000+00:00", lastTs: "2026-10-01T00:14:00.000000+00:00" },
  ],
  feeds: [{ feed: "btc_spot", key: "BTC-USD", values: 30, firstKnownAt: "2026-10-01T00:00:00.000000+00:00", lastKnownAt: "2026-10-01T00:29:00.000000+00:00" }],
  newestTs: "2026-10-01T00:29:00.000000+00:00",
};

function market(ticker: string, series = "KXBTC15M", result: "yes" | "no" | null = "yes"): DbMarket {
  return {
    ticker,
    series,
    windowId: ticker,
    openTime: "2026-10-01T00:00:00.000000+00:00",
    closeTime: "2026-10-01T00:15:00.000000+00:00",
    snapshots: 3,
    firstTs: "2026-10-01T00:00:00.000000+00:00",
    lastTs: "2026-10-01T00:02:00.000000+00:00",
    strikeType: null,
    floorStrike: null,
    capStrike: null,
    result,
    settledAt: result === null ? null : "2026-10-01T00:15:05.000000+00:00",
  };
}

const POINTS: DbTopPoint[] = [
  { id: 1, t: T0, yesBid: 0.2, yesAsk: 0.21, noBid: 0.79, noAsk: 0.8 },
  { id: 2, t: T0 + 60, yesBid: 0.25, yesAsk: 0.26, noBid: 0.74, noAsk: 0.75 },
  { id: 3, t: T0 + 120, yesBid: 0.3, yesAsk: 0.32, noBid: 0.68, noAsk: 0.7 },
];

interface Calls {
  markets: unknown[];
  depth: (number | undefined)[];
  feed: unknown[];
  attach: string[];
  save: unknown[];
  sync: number;
}

const ok = <T,>(value: T): Promise<DbResult<T>> => Promise.resolve({ ok: true, value });

function fake(o: { info?: DbResult<DbInfo>; vps?: Partial<VpsApi> } = {}): Calls {
  const calls: Calls = { markets: [], depth: [], feed: [], attach: [], save: [], sync: 0 };
  const db: DbApi = {
    info: () => Promise.resolve(o.info ?? { ok: true, value: INFO }),
    markets: (q) => {
      calls.markets.push(q);
      const rows = q.series === "KXETH15M" ? [market("KXETH15M-A", "KXETH15M", null)] : [market("KXBTC15M-B"), market("KXBTC15M-A", "KXBTC15M", "no")];
      const filtered = rows.filter((r) => q.text === undefined || q.text === "" || r.ticker.includes(q.text));
      return ok({ total: filtered.length, offset: 0, rows: filtered });
    },
    top: (ticker) => ok({ ticker, points: POINTS, truncated: false }),
    depth: (ticker, id) => {
      calls.depth.push(id);
      const p = POINTS.find((x) => x.id === id) ?? POINTS[2];
      return ok({
        id: p?.id ?? 3,
        ticker,
        ts: "2026-10-01T00:02:00.000000+00:00",
        yes: [[p?.yesBid ?? 0.3, 50], [0.19, 60]],
        no: [[p?.noBid ?? 0.68, 40]],
      });
    },
    feed: (q) => {
      calls.feed.push(q);
      return ok({ feed: q.feed, key: q.key, points: [{ knownAt: T0, value: 100 }, { knownAt: T0 + 30, value: 101 }], truncated: false });
    },
  };
  const vpsState: VpsState = { config: null, cacheDir: "/userData/vps-cache", syncing: false, lastSync: null };
  const state = { sources: [{ id: "sample", label: "Sample data", kind: "sample" as const, path: "/fixtures/basic" }], activeId: "sample", sampleAvailable: true, dbPaths: { sample: "/fixtures/basic/nem.db" } };
  const api = {
    appVersion: () => Promise.resolve("test"),
    listSources: () => Promise.resolve(state.sources),
    data: { snapshot: () => Promise.resolve({ state: "none" }), onChanged: () => () => undefined, db },
    sources: {
      list: () => Promise.resolve(state),
      attachDb: (id: string) => {
        calls.attach.push(id);
        return Promise.resolve({ ok: false, state, message: "cancelled" });
      },
      detachDb: () => Promise.resolve(state),
      vps: {
        get: () => Promise.resolve(vpsState),
        save: (c) => {
          calls.save.push(c);
          return Promise.resolve({ ok: false, state: vpsState, error: { kind: "invalid", field: "host", message: "Host: a hostname or IPv4 address." } });
        },
        forget: () => Promise.resolve(vpsState),
        sync: () => {
          calls.sync++;
          return Promise.resolve({ state: vpsState, sync: { at: "2026-11-20T12:00:00.000Z", ok: false, steps: [] } });
        },
        pickKey: () => Promise.resolve(null),
        ...o.vps,
      } satisfies VpsApi,
    },
    kalshi: {},
    platform: "linux",
  } as unknown as NemApi;
  window.nem = api;
  return calls;
}

function mount(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  delete window.nem;
  act(() => useNav.setState({ screen: "portfolios", params: {}, history: [] }));
});

describe("chart helpers", () => {
  it("depth curves accumulate from the best price, asks implied from NO bids", () => {
    const { bids, asks } = depthCurves(
      [
        [0.19, 60],
        [0.2, 50],
      ],
      [
        [0.79, 50],
        [0.78, 60],
      ],
    );
    expect(bids).toEqual([
      { price: 0.2, cum: 50 },
      { price: 0.19, cum: 110 },
    ]);
    expect(asks).toEqual([
      { price: 0.21, cum: 50 },
      { price: 0.22, cum: 110 },
    ]);
  });

  it("aligns snapshots and feed values on one time axis, in cents", () => {
    const a = alignSeries(POINTS.slice(0, 2), [{ knownAt: T0 + 30, value: 7 }]);
    expect(a.x).toEqual([T0, T0 + 30, T0 + 60]);
    expect(a.bid).toEqual([20, null, 25]);
    expect(a.ask).toEqual([21, null, 26]);
    expect(a.feed).toEqual([null, 7, null]);
    expect(nearestPoint(POINTS, T0 + 100)?.id).toBe(3);
  });
});

describe("Market screen", () => {
  it("says why there is no data and links to settings", async () => {
    const err: DbError = { kind: "no_db", message: "Sample data has no database." };
    fake({ info: { ok: false, error: err } });
    mount(<MarketScreen />);
    const empty = await screen.findByRole("status", { name: "No market data" });
    expect(empty).toHaveTextContent("data/snapshot/nem.db");
    fireEvent.click(within(empty).getByRole("button", { name: "Open Sources and Settings" }));
    expect(useNav.getState().screen).toBe("settings");
  });

  it("shows a schema error with expected and found versions", async () => {
    fake({ info: { ok: false, error: { kind: "schema_version", message: "v9", file: "/x/nem.db", expected: "3", found: "9" } } });
    mount(<MarketScreen />);
    const e = await screen.findByRole("alert", { name: "Schema error" });
    expect(e).toHaveTextContent("PRAGMA user_version");
    expect(e).toHaveTextContent("9");
  });

  it("shows other database errors as states, not crashes", async () => {
    fake({ info: { ok: false, error: { kind: "locked", message: "Database is locked", file: "/x/nem.db" } } });
    mount(<MarketScreen />);
    expect(await screen.findByRole("alert", { name: "Database unavailable" })).toHaveTextContent("locked");
  });

  it("lists markets, shows depth levels, switches series, moves with j/k, overlays a feed", async () => {
    const calls = fake();
    mount(<MarketScreen />);
    expect(await screen.findByTestId("market-db")).toHaveTextContent("schema v3");
    expect(screen.getByRole("alert")).toHaveTextContent("STALE");
    const detail = await screen.findByRole("region", { name: "Market KXBTC15M-B" });
    expect(within(detail).getByTestId("market-result")).toHaveTextContent("YES");
    const levels = await within(detail).findByRole("table", { name: "Order book levels" });
    expect(levels).toHaveTextContent("30c");
    expect(levels).toHaveTextContent("68c");
    expect(within(detail).getByTestId("market-snap-top")).toHaveTextContent("bid 30c / ask 32c");

    fireEvent.change(within(detail).getByLabelText("Snapshot"), { target: { value: "0" } });
    await waitFor(() => expect(calls.depth).toContain(1));
    expect(within(detail).getByTestId("market-snap-top")).toHaveTextContent("bid 20c / ask 21c");

    fireEvent.change(within(detail).getByLabelText("Feed overlay"), { target: { value: "btc_spot/BTC-USD" } });
    await waitFor(() => expect(calls.feed).toEqual([{ feed: "btc_spot", key: "BTC-USD", from: T0 - 60, to: T0 + 120 }]));
    expect(await screen.findByText(/btc_spot BTC-USD at known_at/)).toBeInTheDocument();
    expect(detail).toHaveTextContent("not the settlement source");

    fireEvent.keyDown(window, { key: "j" });
    expect(await screen.findByRole("region", { name: "Market KXBTC15M-A" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /KXETH15M/ }));
    const eth = await screen.findByRole("region", { name: "Market KXETH15M-A" });
    expect(within(eth).getByTestId("market-result")).toHaveTextContent("not settled");
  });

  it("filters tickers in main (the query carries the text)", async () => {
    const calls = fake();
    mount(<MarketScreen />);
    await screen.findByRole("region", { name: "Market KXBTC15M-B" });
    fireEvent.change(screen.getByLabelText("Filter tickers"), { target: { value: "-A" } });
    await waitFor(() => expect(calls.markets).toContainEqual(expect.objectContaining({ text: "-A" })));
    expect(await screen.findByTestId("market-count")).toHaveTextContent("1 of 1");
  });

  it("opens the market given in navigation params", async () => {
    fake();
    act(() => useNav.setState({ screen: "market", params: { series: "KXBTC15M", ticker: "KXBTC15M-A" } }));
    mount(<MarketScreen />);
    expect(await screen.findByRole("region", { name: "Market KXBTC15M-A" })).toBeInTheDocument();
  });
});

describe("Settings: databases and VPS sync", () => {
  it("shows each source's database and attaches one", async () => {
    const calls = fake();
    mount(<SettingsScreen />);
    expect(await screen.findByTestId("source-db-sample")).toHaveTextContent("DB: nem.db");
    fireEvent.click(screen.getByRole("button", { name: "Attach database to Sample data" }));
    await waitFor(() => expect(calls.attach).toEqual(["sample"]));
  });

  it("shows a field error from main and does not sync an invalid config", async () => {
    const calls = fake();
    mount(<SettingsScreen />);
    const section = await screen.findByRole("region", { name: "VPS sync" });
    expect(section).toHaveTextContent("known_hosts");
    fireEvent.change(within(section).getByLabelText("Host"), { target: { value: "bad host" } });
    fireEvent.click(within(section).getByRole("button", { name: "Sync now" }));
    expect(await within(section).findByText("Host: a hostname or IPv4 address.")).toBeInTheDocument();
    expect(calls.save).toEqual([expect.objectContaining({ host: "bad host", port: 22, remoteBase: "/home/nem/not-enough-markets", intervalMinutes: null })]);
    expect(calls.sync).toBe(0);
  });

  it("shows the last sync's host key failure", async () => {
    const state: VpsState = {
      config: { host: "h.example", user: "root", port: 22, keyPath: "/k", remoteBase: "/home/nem/not-enough-markets", intervalMinutes: 15 },
      cacheDir: "/c",
      syncing: false,
      lastSync: {
        at: "2026-11-20T12:00:00.000Z",
        ok: false,
        steps: [{ what: "out", ok: false }],
        error: { kind: "host_key", message: "The host key for h.example is not in your known_hosts", detail: "Host key verification failed." },
      },
    };
    fake({ vps: { get: () => Promise.resolve(state) } });
    mount(<SettingsScreen />);
    const last = await screen.findByTestId("vps-last");
    expect(last).toHaveTextContent("failed");
    expect(last).toHaveTextContent("not in your known_hosts");
    expect(screen.getByLabelText("Auto sync")).toHaveValue("15");
    expect(screen.getByRole("button", { name: "Forget server" })).toBeInTheDocument();
  });
});
