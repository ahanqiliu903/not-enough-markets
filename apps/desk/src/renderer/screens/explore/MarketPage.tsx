import { useState } from "react";
import { impliedAsks, type BookLevel, type OrderBook } from "@shared/kalshi";
import { countdown, formatEt, formatPrice, formatQty, isOpen, marketRules } from "./format";
import { POLL_MS, useMarket, useNow, useOrderbook, useSeriesInfo } from "./hooks";
import { KalshiErrorState, Loading, loaded, RulesCard, StartStrategy } from "./shared";
import { MAX_SAMPLES, TopOfBookChart, type TopSample } from "./TopOfBookChart";

export const DEPTH_LEVELS = 5;

/** One market: YES and NO panels (instant buy / instant sell, top 5 levels) and a chart. */
export function MarketPage({ series, ticker }: { series: string; ticker: string }) {
  const now = useNow();
  const marketQ = useMarket(ticker, true);
  const market = loaded(marketQ);
  const open = market.state !== "ok" || isOpen(market.data, now);
  const book = loaded(useOrderbook(ticker, open));
  const info = loaded(useSeriesInfo(series));
  const samples = useTopSamples(book.state === "ok" ? book : null);

  if (market.state === "loading") return <Loading what="market" />;
  if (market.state === "error") return <KalshiErrorState error={market.error} />;
  const m = market.data;
  const rules = marketRules(m, info.state === "ok" ? info.data : null, series);

  return (
    <div className="screen-stack">
      <header className="explore-header">
        <h2 className="section-title pixel-font">Market {m.ticker}</h2>
        <RulesCard content={rules} />
      </header>
      <dl className="kv market-facts">
        <dt>Question</dt>
        <dd>{m.title}</dd>
        <dt>YES if</dt>
        <dd>{m.yesSubTitle !== "" ? m.yesSubTitle : rules.stats[1]?.value}</dd>
        <dt>Closes</dt>
        <dd>
          <span className="num" data-testid="market-countdown">
            {countdown(m.closeTime, now)}
          </span>{" "}
          <span className="dim">({formatEt(m.closeTime)})</span>
        </dd>
        <dt>Status</dt>
        <dd>
          {m.status}
          {open ? <span className="dim"> (polling every {POLL_MS.book / 1000}s)</span> : <span className="dim"> (not polling)</span>}
        </dd>
      </dl>

      <h3 className="section-title pixel-font">Order book</h3>
      {book.state === "loading" ? (
        <Loading what="order book" />
      ) : book.state === "error" ? (
        <KalshiErrorState error={book.error} />
      ) : (
        <div className="book-columns">
          <SidePanel side="YES" own={book.data.yes} other={book.data.no} />
          <SidePanel side="NO" own={book.data.no} other={book.data.yes} />
        </div>
      )}
      <TopOfBookChart samples={samples} />
      <StartStrategy series={series} />
    </div>
  );
}

/** Kalshi books hold bids only: buying YES lifts NO bids at 1 - p, selling YES hits YES bids. */
function SidePanel({ side, own, other }: { side: "YES" | "NO"; own: readonly BookLevel[]; other: readonly BookLevel[] }) {
  const asks = impliedAsks(other).slice(0, DEPTH_LEVELS);
  const bids = own.slice(0, DEPTH_LEVELS);
  return (
    <section className="book-side pixel-border" aria-label={`${side} side`}>
      <h4 className="book-side-title pixel-font">{side}</h4>
      <div className="book-best">
        <span>
          <span className="dim">Instant buy </span>
          <span className="num book-best-price" data-testid={`${side.toLowerCase()}-buy`}>
            {formatPrice(asks[0]?.price ?? null)}
          </span>
        </span>
        <span>
          <span className="dim">Instant sell </span>
          <span className="num book-best-price" data-testid={`${side.toLowerCase()}-sell`}>
            {formatPrice(bids[0]?.price ?? null)}
          </span>
        </span>
      </div>
      <Ladder label={`Buy ${side} (asks, best first)`} levels={asks} />
      <Ladder label={`Sell ${side} (bids, best first)`} levels={bids} />
    </section>
  );
}

function Ladder({ label, levels }: { label: string; levels: readonly BookLevel[] }) {
  return (
    <table className="grid book-ladder" aria-label={label}>
      <caption className="dim">{label}</caption>
      <thead>
        <tr>
          <th className="num">Price</th>
          <th className="num">Qty</th>
        </tr>
      </thead>
      <tbody>
        {levels.length === 0 ? (
          <tr>
            <td className="dim" colSpan={2}>
              no resting orders
            </td>
          </tr>
        ) : (
          levels.map((l) => (
            <tr key={l.price}>
              <td className="num">{formatPrice(l.price)}</td>
              <td className="num">{formatQty(l.qty)}</td>
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}

/** Appends one YES top-of-book sample per new order book response (derived during render). */
function useTopSamples(book: { data: OrderBook; fetchedAt: string } | null): TopSample[] {
  const [state, setState] = useState<{ key: string | null; samples: TopSample[] }>({ key: null, samples: [] });
  const fetchedAt = book?.fetchedAt;
  if (book !== null && fetchedAt !== undefined && fetchedAt !== state.key) {
    const t = Date.parse(fetchedAt);
    const sample: TopSample = {
      t,
      bid: book.data.yes[0]?.price ?? null,
      ask: impliedAsks(book.data.no.slice(0, 1))[0]?.price ?? null,
    };
    setState({ key: fetchedAt, samples: [...state.samples, sample].slice(-MAX_SAMPLES) });
  }
  return state.samples;
}
