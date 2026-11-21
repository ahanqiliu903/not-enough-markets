import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { DbFeedQuery } from "@shared/db";
import { nem, queryKeys } from "../../lib/api";

/** DESK-011 queries. All under `queryKeys.db` so a data:changed push refetches them. */

export const MARKETS_PAGE = 200;

export function useDbInfo() {
  return useQuery({ queryKey: [...queryKeys.db, "info"], queryFn: () => nem().data.db.info() });
}

export function useDbMarkets(series: string | null, text: string) {
  return useQuery({
    queryKey: [...queryKeys.db, "markets", series, text],
    queryFn: () => nem().data.db.markets({ series: series ?? "", text, offset: 0, limit: MARKETS_PAGE }),
    enabled: series !== null,
    placeholderData: keepPreviousData,
  });
}

export function useDbTop(ticker: string | null) {
  return useQuery({
    queryKey: [...queryKeys.db, "top", ticker],
    queryFn: () => nem().data.db.top(ticker ?? ""),
    enabled: ticker !== null,
  });
}

export function useDbDepth(ticker: string | null, id: number | undefined) {
  return useQuery({
    queryKey: [...queryKeys.db, "depth", ticker, id ?? "latest"],
    queryFn: () => nem().data.db.depth(ticker ?? "", id),
    enabled: ticker !== null,
    placeholderData: keepPreviousData,
  });
}

export function useDbFeed(q: DbFeedQuery | null) {
  return useQuery({
    queryKey: [...queryKeys.db, "feed", q],
    queryFn: () => (q === null ? Promise.reject(new Error("no feed")) : nem().data.db.feed(q)),
    enabled: q !== null,
  });
}
