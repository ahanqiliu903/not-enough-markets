import { useMemo, useState } from "react";
import type { SeriesListItem } from "@shared/kalshi";
import { PixelIcon } from "../../components/PixelIcon";
import { useKeys } from "../../lib/keys";
import { CATEGORIES, SEARCH_TAB, seedFor, type SeedSeries } from "./catalog";
import { countdown, isOpen, seriesRules } from "./format";
import { POLL_MS, useDebounced, useMarkets, useNow, useSeriesSearch } from "./hooks";
import { KalshiErrorState, Loading, loaded, RulesCard } from "./shared";

/** Remembers the last tab for this session, so Esc back from a series lands where you were. */
const tabMemory: { last?: string } = {};
function rememberTab(id: string): void {
  tabMemory.last = id;
}

const TABS = [...CATEGORIES.map((c) => ({ id: c.id, label: c.label, icon: c.icon })), SEARCH_TAB];

/** Category tabs on the left, series tiles (or search results) on the right. */
export function Browse({
  initialTab,
  onOpenSeries,
}: {
  initialTab: string | undefined;
  onOpenSeries: (series: string, tab: string) => void;
}) {
  const [tab, setTabState] = useState(() => {
    const want = initialTab ?? tabMemory.last;
    return TABS.some((t) => t.id === want) ? (want ?? "") : (TABS[0]?.id ?? "");
  });
  const setTab = (id: string) => {
    rememberTab(id);
    setTabState(id);
  };
  const category = CATEGORIES.find((c) => c.id === tab);

  return (
    <div className="explore-browse">
      <div className="explore-tabs" role="tablist" aria-orientation="vertical" aria-label="Categories">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`explore-tab-${t.id}`}
            aria-selected={t.id === tab}
            aria-controls="explore-tabpanel"
            className={`explore-tab pixel-border${t.id === tab ? " active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            <PixelIcon name={t.icon} />
            <span className="explore-tab-label pixel-font">{t.label}</span>
          </button>
        ))}
      </div>
      <div className="explore-panel" role="tabpanel" id="explore-tabpanel" aria-labelledby={`explore-tab-${tab}`}>
        {category !== undefined ? (
          <SeriesGrid
            key={category.id}
            series={category.series}
            onOpen={(s) => onOpenSeries(s, category.id)}
            note={category.predictor}
          />
        ) : (
          <SeriesSearch onOpen={(s) => onOpenSeries(s, SEARCH_TAB.id)} />
        )}
      </div>
    </div>
  );
}

function useListKeys(count: number, onEnter: (i: number) => void): [number, (i: number) => void] {
  const [active, setActive] = useState(0);
  const idx = Math.min(active, Math.max(0, count - 1));
  useKeys({
    j: () => setActive(Math.min(idx + 1, count - 1)),
    k: () => setActive(Math.max(idx - 1, 0)),
    Enter: () => {
      if (count === 0) return false;
      onEnter(idx);
    },
  });
  return [idx, setActive];
}

function SeriesGrid({
  series,
  onOpen,
  note,
}: {
  series: readonly SeedSeries[];
  onOpen: (ticker: string) => void;
  note: string;
}) {
  const [active, setActive] = useListKeys(series.length, (i) => {
    const s = series[i];
    if (s !== undefined) onOpen(s.ticker);
  });
  const now = useNow();
  return (
    <>
      <p className="dim explore-note">{note}</p>
      <ul className="series-grid" aria-label="Series">
        {series.map((s, i) => (
          <SeriesTile
            key={s.ticker}
            seed={s}
            active={i === active}
            now={now}
            onFocus={() => setActive(i)}
            onOpen={() => onOpen(s.ticker)}
          />
        ))}
      </ul>
    </>
  );
}

function SeriesTile({
  seed,
  active,
  now,
  onFocus,
  onOpen,
}: {
  seed: SeedSeries;
  active: boolean;
  now: number;
  onFocus: () => void;
  onOpen: () => void;
}) {
  const markets = loaded(useMarkets(seed.ticker, POLL_MS.grid));

  let count = "...";
  let next = "";
  if (markets.state === "ok") {
    const open = markets.data.filter((m) => isOpen(m, now));
    count = String(open.length);
    const soonest = open.map((m) => m.closeTime).sort()[0];
    next = soonest !== undefined ? countdown(soonest, now) : "none open";
  } else if (markets.state === "error") {
    count = "--";
    next = markets.error.kind.replace("_", " ");
  }
  const cat = CATEGORIES.find((c) => c.series.includes(seed));
  return (
    <li className={`series-tile pixel-border${active ? " active" : ""}`} data-series={seed.ticker}>
      <button
        type="button"
        className="series-tile-open"
        aria-label={`Open series ${seed.ticker}`}
        onClick={onOpen}
        onFocus={onFocus}
      >
        <span className="series-tile-icon">{cat !== undefined && <PixelIcon name={cat.icon} size={32} />}</span>
        <span className="series-tile-ticker">{seed.ticker}</span>
        <span className="series-tile-label dim">{seed.label}</span>
        <span className="series-tile-stats">
          <span>
            <span className="dim">open </span>
            <span className="num" data-testid="open-count">
              {count}
            </span>
          </span>
          <span>
            <span className="dim">next </span>
            <span className="num">{next}</span>
          </span>
        </span>
      </button>
      <span className="series-tile-rules">
        <RulesCard content={seriesRules(null, seed.ticker)} label="?" />
      </span>
    </li>
  );
}

function SeriesSearch({ onOpen }: { onOpen: (ticker: string) => void }) {
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query);
  const result = loaded(useSeriesSearch(debounced));
  const items: SeriesListItem[] = useMemo(() => (result.state === "ok" ? result.data : []), [result]);
  const [active, setActive] = useListKeys(items.length, (i) => {
    const s = items[i];
    if (s !== undefined) onOpen(s.ticker);
  });
  return (
    <div className="series-search">
      <input
        className="explore-search"
        data-screen-filter
        aria-label="Search any series"
        placeholder="Search any series (ticker, title, category)"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && items[active] !== undefined) onOpen(items[active].ticker);
        }}
      />
      {debounced.trim().length < 2 ? (
        <p className="dim">Type at least 2 characters. The full series list is fetched once and cached for an hour.</p>
      ) : result.state === "loading" ? (
        <Loading what="series" />
      ) : result.state === "error" ? (
        <KalshiErrorState error={result.error} />
      ) : items.length === 0 ? (
        <p className="dim">No series match "{debounced}".</p>
      ) : (
        <table className="grid explore-table" aria-label="Search results">
          <thead>
            <tr>
              <th>Series</th>
              <th>Title</th>
              <th>Category</th>
              <th>Frequency</th>
            </tr>
          </thead>
          <tbody>
            {items.map((s, i) => (
              <tr
                key={s.ticker}
                aria-selected={i === active}
                className={i === active ? "active" : undefined}
                onMouseMove={() => setActive(i)}
                onClick={() => onOpen(s.ticker)}
              >
                <td>
                  <button type="button" className="link-button">
                    {s.ticker}
                  </button>
                  {seedFor(s.ticker) !== undefined && <span className="dim"> (seeded)</span>}
                </td>
                <td>{s.title}</td>
                <td className="dim">{s.category}</td>
                <td className="dim">{s.frequency}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
