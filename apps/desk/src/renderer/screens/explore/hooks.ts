import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { KalshiMarket, KalshiResult, KalshiSeries, OrderBook, SeriesListItem } from "@shared/kalshi";
import { nem, queryKeys } from "../../lib/api";

/**
 * Explore data hooks. Main caches responses (5s markets/books, 10m series) and rate-limits
 * to 5 req/s, so these intervals stay polite even with many tiles on screen.
 */
export const POLL_MS = { book: 5_000, list: 15_000, grid: 30_000 } as const;

type Q<T> = UseQueryResult<KalshiResult<T>>;

const base = { retry: false, staleTime: 4_000 } as const;

export function useSeriesInfo(ticker: string | undefined): Q<KalshiSeries> {
  return useQuery({
    ...base,
    queryKey: [...queryKeys.kalshi, "series", ticker],
    queryFn: () => nem().kalshi.series(ticker ?? ""),
    enabled: ticker !== undefined,
    staleTime: 10 * 60_000,
  });
}

export function useMarkets(series: string | undefined, refetchMs: number | false = POLL_MS.list): Q<KalshiMarket[]> {
  return useQuery({
    ...base,
    queryKey: [...queryKeys.kalshi, "markets", series],
    queryFn: () => nem().kalshi.markets(series ?? ""),
    enabled: series !== undefined,
    refetchInterval: refetchMs,
  });
}

export function useMarket(ticker: string | undefined, poll: boolean): Q<KalshiMarket> {
  return useQuery({
    ...base,
    queryKey: [...queryKeys.kalshi, "market", ticker],
    queryFn: () => nem().kalshi.market(ticker ?? ""),
    enabled: ticker !== undefined,
    refetchInterval: poll ? POLL_MS.book : false,
  });
}

export function useOrderbook(ticker: string | undefined, poll: boolean): Q<OrderBook> {
  return useQuery({
    ...base,
    queryKey: [...queryKeys.kalshi, "orderbook", ticker],
    queryFn: () => nem().kalshi.orderbook(ticker ?? ""),
    enabled: ticker !== undefined,
    refetchInterval: poll ? POLL_MS.book : false,
  });
}

export function useSeriesSearch(query: string): Q<SeriesListItem[]> {
  const q = query.trim();
  return useQuery({
    ...base,
    queryKey: [...queryKeys.kalshi, "search", q],
    queryFn: () => nem().kalshi.searchSeries(q),
    enabled: q.length >= 2,
    staleTime: 60_000,
  });
}

/** Wall clock that ticks every `ms` (drives countdowns). */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

/** Debounced copy of `value`. */
export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}
