import { isTicker } from "@shared/kalshi";
import { useNav } from "../../state/nav";
import { Browse } from "./Browse";
import { MarketList } from "./MarketList";
import { MarketPage } from "./MarketPage";
import "./explore.css";

/**
 * Screen 9: Explore Kalshi's public markets (read-only; no key, no account, no orders).
 * Params: `{ tab? }` browse, `{ series }` market list, `{ series, market }` market page.
 * Esc goes back (shell), j/k/Enter move and open inside lists.
 */
export default function ExploreScreen() {
  const params = useNav((s) => s.params);
  const navigate = useNav((s) => s.navigate);
  const series = isTicker(params["series"]) ? params["series"] : undefined;
  const market = isTicker(params["market"]) ? params["market"] : undefined;

  return (
    <div className="explore">
      <nav className="explore-crumbs" aria-label="Explore breadcrumbs">
        <button type="button" className="link-button" onClick={() => navigate("explore", params["tab"] !== undefined ? { tab: params["tab"] } : {})}>
          Explore
        </button>
        {series !== undefined && (
          <>
            <span className="dim"> / </span>
            <button type="button" className="link-button" onClick={() => navigate("explore", { series })}>
              Series {series}
            </button>
          </>
        )}
        {series !== undefined && market !== undefined && (
          <>
            <span className="dim"> / </span>
            <span>Market {market}</span>
          </>
        )}
        <span className="explore-crumbs-note dim">Public Kalshi data, read-only</span>
      </nav>
      {series !== undefined && market !== undefined ? (
        <MarketPage key={market} series={series} ticker={market} />
      ) : series !== undefined ? (
        <MarketList key={series} series={series} onOpenMarket={(t) => navigate("explore", { series, market: t })} />
      ) : (
        <Browse initialTab={params["tab"]} onOpenSeries={(s, tab) => navigate("explore", { series: s, tab })} />
      )}
    </div>
  );
}
