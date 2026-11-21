import { useMemo, useState } from "react";
import type { KalshiMarket } from "@shared/kalshi";
import { StateTitle } from "../../components/states";
import { useKeys } from "../../lib/keys";
import { seedFor } from "./catalog";
import { countdown, formatEt, formatPrice, isOpen, marketRules, seriesRules } from "./format";
import { POLL_MS, useMarkets, useNow, useSeriesInfo } from "./hooks";
import { KalshiErrorState, Loading, loaded, RulesCard, StartStrategy } from "./shared";

export type MarketSort = "closing" | "ticker" | "yesAsk";

const SORTS: { id: MarketSort; label: string }[] = [
  { id: "closing", label: "Closing soon" },
  { id: "ticker", label: "Ticker" },
  { id: "yesAsk", label: "YES ask (high first)" },
];

export function sortMarkets(markets: readonly KalshiMarket[], sort: MarketSort): KalshiMarket[] {
  const out = [...markets];
  if (sort === "closing") out.sort((a, b) => Date.parse(a.closeTime) - Date.parse(b.closeTime) || a.ticker.localeCompare(b.ticker));
  else if (sort === "ticker") out.sort((a, b) => a.ticker.localeCompare(b.ticker));
  else out.sort((a, b) => (b.yesAsk ?? -1) - (a.yesAsk ?? -1) || a.ticker.localeCompare(b.ticker));
  return out;
}

/** Open markets of one series with live countdowns to close. */
export function MarketList({ series, onOpenMarket }: { series: string; onOpenMarket: (ticker: string) => void }) {
  const info = loaded(useSeriesInfo(series));
  const markets = loaded(useMarkets(series, POLL_MS.list));
  const [sort, setSort] = useState<MarketSort>("closing");
  const [active, setActive] = useState(0);
  const now = useNow();
  const seriesData = info.state === "ok" ? info.data : null;

  const rows = useMemo(() => (markets.state === "ok" ? sortMarkets(markets.data, sort) : []), [markets, sort]);
  const idx = Math.min(active, Math.max(0, rows.length - 1));
  useKeys({
    j: () => setActive(Math.min(idx + 1, rows.length - 1)),
    k: () => setActive(Math.max(idx - 1, 0)),
    Enter: () => {
      const m = rows[idx];
      if (m === undefined) return false;
      onOpenMarket(m.ticker);
    },
  });

  const seed = seedFor(series);
  return (
    <div className="screen-stack">
      <header className="explore-header">
        <h2 className="section-title pixel-font">Series {series}</h2>
        <span className="dim">{seriesData?.title ?? seed?.label ?? ""}</span>
        <RulesCard content={seriesRules(seriesData, series)} />
        {info.state === "error" && info.error.kind !== "not_found" && (
          <span className="dim">(series details unavailable: {info.error.kind.replace("_", " ")})</span>
        )}
      </header>
      <StartStrategy series={series} />
      <div className="row-wrap">
        <label className="dim" htmlFor="explore-sort">
          Sort
        </label>
        <select id="explore-sort" value={sort} onChange={(e) => setSort(e.target.value as MarketSort)}>
          {SORTS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
        {markets.state === "ok" && (
          <span className="dim">
            {rows.length} open market{rows.length === 1 ? "" : "s"}, refreshed every {POLL_MS.list / 1000}s
          </span>
        )}
      </div>
      {markets.state === "loading" ? (
        <Loading what="markets" />
      ) : markets.state === "error" ? (
        <KalshiErrorState error={markets.error} />
      ) : rows.length === 0 ? (
        <section className="state state-empty" role="status" aria-label="No open markets">
          <StateTitle kind="empty">No open markets</StateTitle>
          <p>Kalshi lists no open markets for {series} right now. Daily series open new markets each day.</p>
        </section>
      ) : (
        <table className="grid explore-table" aria-label="Markets">
          <thead>
            <tr>
              <th>Market</th>
              <th>YES if</th>
              <th className="num">YES bid</th>
              <th className="num">YES ask</th>
              <th className="num">NO bid</th>
              <th className="num">NO ask</th>
              <th className="num">Closes in</th>
              <th>Close (ET)</th>
              <th>Rules</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m, i) => (
              <tr
                key={m.ticker}
                aria-selected={i === idx}
                className={`${i === idx ? "active" : ""}${isOpen(m, now) ? "" : " dim"}`}
                onMouseMove={() => setActive(i)}
                onClick={() => onOpenMarket(m.ticker)}
              >
                <td>
                  <button type="button" className="link-button" onFocus={() => setActive(i)}>
                    {m.ticker}
                  </button>
                </td>
                <td>{m.yesSubTitle}</td>
                <td className="num">{formatPrice(m.yesBid)}</td>
                <td className="num">{formatPrice(m.yesAsk)}</td>
                <td className="num">{formatPrice(m.noBid)}</td>
                <td className="num">{formatPrice(m.noAsk)}</td>
                <td className="num" data-testid="countdown">
                  {countdown(m.closeTime, now)}
                </td>
                <td className="dim">{formatEt(m.closeTime)}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  <RulesCard content={marketRules(m, seriesData, series)} label="?" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
