import { useCallback, useMemo, useState } from "react";
import { dbTimeToUnix, type DbError, type DbFeedInfo, type DbInfo, type DbMarket, type DbResult, type DbTopPoint } from "@shared/db";
import { EmptyState, SchemaError, StaleBanner, StateTitle } from "../../components/states";
import { ConnectSource } from "../../data/states";
import { useNow } from "../../data/hooks";
import { useKeys } from "../../lib/keys";
import { useRegisterCommands, type Command } from "../../state/commands";
import { useNav } from "../../state/nav";
import { formatPrice } from "../explore/format";
import { DepthChart } from "./DepthChart";
import { MARKETS_PAGE, useDbDepth, useDbFeed, useDbInfo, useDbMarkets, useDbTop } from "./hooks";
import { TopChart } from "./TopChart";
import "./market.css";

/**
 * Screen 5: Market (DESK-011). Recorded data from the active source's snapshot database:
 * pick a series and market, see its top of book over time (uPlot) with a feed overlaid at
 * `known_at`, and the order book depth of any snapshot (canvas). Prices are shown as
 * recorded; no fills or statistics are computed here.
 */
export default function MarketScreen() {
  const info = useDbInfo();
  const params = useNav((s) => s.params);
  if (info.isPending) return <p className="dim">Reading the database...</p>;
  if (info.isError) return <p role="alert">Could not read the database: {String(info.error)}</p>;
  if (!info.data.ok) return <DbErrorState error={info.data.error} />;
  return <MarketView key={`${params["series"] ?? ""}/${params["ticker"] ?? ""}`} info={info.data.value} params={params} />;
}

export function DbErrorState({ error }: { error: DbError }) {
  if (error.kind === "schema_version") {
    return (
      <SchemaError
        file={error.file ?? "nem.db"}
        field="PRAGMA user_version"
        expectedVersion={error.expected ?? "3"}
        {...(error.found !== undefined ? { foundVersion: error.found } : {})}
        detail={error.message}
      />
    );
  }
  if (error.kind === "no_db") {
    return (
      <EmptyState
        title="No market data"
        reason={`${error.message} Order books, prices and feeds come from the engine's database, read from a consistent snapshot (deploy/snapshot.sh writes data/snapshot/nem.db). Sync it from a VPS or attach a snapshot file in Sources and Settings.`}
        commands={["uv run nem run", "sqlite3 data/nem.db \".backup data/snapshot/nem.db\""]}
      >
        <div className="row-wrap">
          <button type="button" onClick={() => useNav.getState().navigate("settings")}>
            Open Sources and Settings
          </button>
        </div>
        <ConnectSource />
      </EmptyState>
    );
  }
  return (
    <section className="state state-error" role="alert" aria-label="Database unavailable">
      <StateTitle kind="error">Database unavailable</StateTitle>
      <p>{error.message}</p>
      {error.file !== undefined && (
        <p className="dim">
          File: <code>{error.file}</code>
        </p>
      )}
      <p className="dim">Nothing from this database is shown. Fix the file or attach another snapshot in Sources and Settings.</p>
    </section>
  );
}

function short(ts: string): string {
  return ts.slice(0, 19).replace("T", " ");
}

function strike(m: DbMarket): string {
  if (m.strikeType === null) return "not recorded";
  const f = m.floorStrike;
  const c = m.capStrike;
  if (m.strikeType === "between") return `between ${String(f)} and ${String(c)}`;
  return `${m.strikeType} ${String(f ?? c ?? "")}`.trim();
}

function MarketView({ info, params }: { info: DbInfo; params: Readonly<Record<string, string>> }) {
  const now = useNow();
  const initialSeries = info.series.some((s) => s.series === params["series"]) ? (params["series"] ?? null) : (info.series[0]?.series ?? null);
  const [series, setSeries] = useState<string | null>(initialSeries);
  const [ticker, setTicker] = useState<string | null>(params["ticker"] ?? null);
  const [filter, setFilter] = useState("");
  const markets = useDbMarkets(series, filter);
  const page = markets.data;
  const rows = page?.ok === true ? page.value.rows : [];
  const selected = rows.find((m) => m.ticker === ticker) ?? (ticker === null ? rows[0] : undefined) ?? null;
  const selIndex = selected === null ? -1 : rows.indexOf(selected);

  const move = (d: number) => {
    const next = rows[Math.min(rows.length - 1, Math.max(0, selIndex + d))];
    if (next !== undefined) setTicker(next.ticker);
  };
  useKeys({ j: () => move(1), k: () => move(-1) });

  const commands = useMemo<Command[]>(
    () =>
      info.series.map((s) => ({
        id: `market:${s.series}`,
        title: `market ${s.series}`,
        group: "Market",
        keywords: "recorded order book depth snapshot",
        run: () => useNav.getState().navigate("market", { series: s.series }),
      })),
    [info.series],
  );
  useRegisterCommands(commands);

  if (info.series.length === 0) {
    return (
      <EmptyState
        title="No snapshots recorded"
        reason={`${info.path} has no order book snapshots yet. The engine records them while it runs.`}
        commands={["uv run nem run"]}
      />
    );
  }

  return (
    <div className="screen-stack market-screen">
      <StaleBanner newest={info.newestTs !== null ? new Date(dbTimeToUnix(info.newestTs) * 1000) : null} now={now} source="the snapshot database" />
      <p className="dim market-db" data-testid="market-db">
        {info.path} · schema v{info.schemaVersion} · {(info.sizeBytes / 1e6).toFixed(1)} MB · newest snapshot{" "}
        {info.newestTs !== null ? short(info.newestTs) : "none"} UTC
      </p>
      <div className="segmented market-series" role="tablist" aria-label="Series">
        {info.series.map((s) => (
          <button
            key={s.series}
            type="button"
            role="tab"
            aria-selected={s.series === series}
            className={s.series === series ? "active" : ""}
            onClick={() => {
              setSeries(s.series);
              setTicker(null);
            }}
          >
            {s.series} <span className="dim">{s.markets}</span>
          </button>
        ))}
      </div>
      <div className="market-layout">
        <section className="market-list" aria-label="Markets">
          <div className="toolbar">
            <input
              type="search"
              placeholder="Filter tickers"
              aria-label="Filter tickers"
              data-screen-filter
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <span className="dim" data-testid="market-count">
              {page?.ok === true ? `${Math.min(page.value.total, MARKETS_PAGE)} of ${page.value.total}` : ""}
            </span>
          </div>
          {page?.ok === false ? (
            <DbErrorState error={page.error} />
          ) : (
            <div className="market-list-scroll">
              <table className="grid" aria-label="Recorded markets">
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Closes UTC</th>
                    <th className="num">Snaps</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((m) => (
                    <tr
                      key={m.ticker}
                      className={m === selected ? "selectable selected" : "selectable"}
                      aria-selected={m === selected}
                      onClick={() => setTicker(m.ticker)}
                    >
                      <td>{m.ticker}</td>
                      <td>{short(m.closeTime).slice(5, 16)}</td>
                      <td className="num">{m.snapshots}</td>
                      <td>{m.result === null ? <span className="dim">open</span> : m.result.toUpperCase()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        {selected !== null ? (
          <MarketDetail key={selected.ticker} market={selected} feeds={info.feeds} />
        ) : (
          <p className="dim">Pick a market.</p>
        )}
      </div>
    </div>
  );
}

function feedId(f: DbFeedInfo): string {
  return `${f.feed}/${f.key}`;
}

function valueOf<T>(q: { data?: DbResult<T> | undefined; isPending: boolean }): T | DbError | null {
  if (q.data === undefined) return null;
  return q.data.ok ? q.data.value : q.data.error;
}

function isError(v: unknown): v is DbError {
  return typeof v === "object" && v !== null && "kind" in v && "message" in v;
}

function MarketDetail({ market, feeds }: { market: DbMarket; feeds: readonly DbFeedInfo[] }) {
  const top = valueOf(useDbTop(market.ticker));
  const [feedKey, setFeedKey] = useState<string>("");
  const [snapId, setSnapId] = useState<number | undefined>(undefined);
  const feed = feeds.find((f) => feedId(f) === feedKey) ?? null;
  const points = top !== null && !isError(top) ? top.points : null;
  const first = points?.[0]?.t;
  const last = points?.at(-1)?.t;
  const feedQuery =
    feed !== null && first !== undefined && last !== undefined
      ? { feed: feed.feed, key: feed.key, from: first - 60, to: last }
      : null;
  const feedData = valueOf(useDbFeed(feedQuery));
  const depth = valueOf(useDbDepth(market.ticker, snapId));
  const onPick = useCallback((p: DbTopPoint) => setSnapId(p.id), []);
  const selIdx = points === null ? -1 : snapId === undefined ? points.length - 1 : points.findIndex((p) => p.id === snapId);
  const selPoint = points?.[selIdx] ?? null;

  return (
    <section className="market-detail" aria-label={`Market ${market.ticker}`}>
      <h2 className="section-title pixel-font">Market {market.ticker}</h2>
      <dl className="kv market-facts">
        <dt>Window</dt>
        <dd>
          {short(market.openTime)} to {short(market.closeTime)} UTC
        </dd>
        <dt>YES if</dt>
        <dd>{strike(market)}</dd>
        <dt>Result</dt>
        <dd data-testid="market-result">
          {market.result === null ? "not settled in this recording" : `${market.result.toUpperCase()} (settled ${short(market.settledAt ?? "")} UTC)`}
        </dd>
        <dt>Recorded</dt>
        <dd>
          {market.snapshots} snapshots, {short(market.firstTs)} to {short(market.lastTs)} UTC
        </dd>
      </dl>

      <div className="toolbar">
        <label htmlFor="market-feed">Feed overlay</label>
        <select id="market-feed" value={feedKey} onChange={(e) => setFeedKey(e.target.value)}>
          <option value="">none</option>
          {feeds.map((f) => (
            <option key={feedId(f)} value={feedId(f)}>
              {f.feed} {f.key}
            </option>
          ))}
        </select>
        {feed !== null && (
          <span className="dim">
            A feed predicts; it is not the settlement source. Values sit at known_at, when each became knowable.
          </span>
        )}
      </div>

      {top === null ? (
        <p className="dim">Reading snapshots...</p>
      ) : isError(top) ? (
        <DbErrorState error={top} />
      ) : (
        <>
          <TopChart
            points={top.points}
            feed={feedData !== null && !isError(feedData) ? feedData.points : []}
            feedLabel={feed !== null ? `${feed.feed} ${feed.key}` : null}
            selectedT={selPoint?.t ?? null}
            onPick={onPick}
          />
          {feedData !== null && isError(feedData) && <p role="alert">Feed: {feedData.message}</p>}
          {top.truncated && <p className="dim">Showing the newest {top.points.length} snapshots.</p>}
          <div className="toolbar">
            <label htmlFor="market-snap">Snapshot</label>
            <input
              id="market-snap"
              type="range"
              min={0}
              max={Math.max(0, top.points.length - 1)}
              value={Math.max(0, selIdx)}
              onChange={(e) => setSnapId(top.points[Number(e.target.value)]?.id)}
            />
            <span className="num" data-testid="market-snap-top">
              {selPoint !== null
                ? `bid ${formatPrice(selPoint.yesBid)} / ask ${formatPrice(selPoint.yesAsk)}`
                : ""}
            </span>
          </div>
        </>
      )}

      <h3 className="section-title pixel-font">Order book</h3>
      {depth === null ? (
        <p className="dim">Reading depth...</p>
      ) : isError(depth) ? (
        <DbErrorState error={depth} />
      ) : (
        <DepthChart yes={depth.yes} no={depth.no} ts={`${short(depth.ts)} UTC`} />
      )}
    </section>
  );
}
