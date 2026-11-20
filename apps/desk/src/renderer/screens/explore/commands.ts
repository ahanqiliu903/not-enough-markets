import { isTicker } from "@shared/kalshi";
import type { Command } from "../../state/commands";
import { useNav } from "../../state/nav";
import { CATEGORIES } from "./catalog";

/** Command bar: "explore SERIES" for every seeded series. */
export function exploreSeedCommands(): Command[] {
  return CATEGORIES.flatMap((c) =>
    c.series.map((s) => ({
      id: `explore:${s.ticker}`,
      title: `explore ${s.ticker}`,
      group: "Explore",
      keywords: `${c.label} ${s.label} kalshi series`,
      run: () => useNav.getState().navigate("explore", { series: s.ticker }),
    })),
  );
}

/** "explore KXANY" typed in the palette opens any series, seeded or not. */
export function exploreQueryCommand(query: string): Command | null {
  const m = /^\s*explore\s+(\S+)\s*$/i.exec(query);
  const ticker = m?.[1]?.toUpperCase();
  if (ticker === undefined || !isTicker(ticker)) return null;
  return {
    id: `explore-query:${ticker}`,
    title: `explore ${ticker}`,
    group: "Explore",
    run: () => useNav.getState().navigate("explore", { series: ticker }),
  };
}
