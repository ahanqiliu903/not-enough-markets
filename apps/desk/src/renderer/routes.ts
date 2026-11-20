import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { PixelIconName } from "./components/PixelIcon";
import type { RouteFlags } from "./state/nav";

/**
 * The one route table. To add or replace a screen, edit only its folder under
 * `screens/<id>/` (default export = the screen component) and, for a new screen, one
 * entry here. Hotkeys 1-8 follow spec Section 5; 9 is Explore (DESK-024).
 */
export type ScreenId =
  | "portfolios"
  | "strategy"
  | "compare"
  | "trades"
  | "market"
  | "backtests"
  | "status"
  | "settings"
  | "explore"
  | "kitchen-sink";

export interface RouteDef {
  id: ScreenId;
  /** Pane title, pixel font, <= 24 chars. */
  title: string;
  /** Cmd/Ctrl+N. */
  hotkey?: number;
  icon?: PixelIconName;
  /** Words the command palette also matches. */
  keywords?: string;
  component: LazyExoticComponent<ComponentType>;
  /** Hidden routes are left out of nav, palette and hotkeys. */
  hidden?: (flags: RouteFlags) => boolean;
  devOnly?: boolean;
}

export const routes: readonly RouteDef[] = [
  {
    id: "portfolios",
    title: "Portfolios",
    hotkey: 1,
    icon: "portfolio",
    keywords: "home overview verdicts",
    component: lazy(() => import("./screens/portfolios")),
  },
  {
    id: "strategy",
    title: "Strategy",
    hotkey: 2,
    icon: "strategy",
    keywords: "detail calibration win rate break-even",
    component: lazy(() => import("./screens/strategy")),
  },
  {
    id: "compare",
    title: "Compare",
    hotkey: 3,
    keywords: "side by side",
    component: lazy(() => import("./screens/compare")),
  },
  {
    id: "trades",
    title: "Trades",
    hotkey: 4,
    keywords: "fills ledger history",
    component: lazy(() => import("./screens/trades")),
  },
  {
    id: "market",
    title: "Market",
    hotkey: 5,
    keywords: "order book prices series",
    component: lazy(() => import("./screens/market")),
  },
  {
    id: "backtests",
    title: "Backtests",
    hotkey: 6,
    keywords: "replay suggest sweep",
    component: lazy(() => import("./screens/backtests")),
    hidden: (flags) => !flags.hasBacktests,
  },
  {
    id: "status",
    title: "Feeds and Status",
    hotkey: 7,
    icon: "feed",
    keywords: "health heartbeat halts feeds",
    component: lazy(() => import("./screens/status")),
  },
  {
    id: "settings",
    title: "Sources and Settings",
    hotkey: 8,
    keywords: "data source preferences font pixel accents",
    component: lazy(() => import("./screens/settings")),
  },
  {
    id: "explore",
    title: "Explore",
    hotkey: 9,
    icon: "explore",
    keywords: "kalshi markets series order book browse research",
    component: lazy(() => import("./screens/explore")),
  },
  {
    id: "kitchen-sink",
    title: "Kitchen Sink",
    keywords: "tokens theme design dev",
    component: lazy(() => import("./screens/kitchen-sink")),
    devOnly: true,
  },
];

export function visibleRoutes(flags: RouteFlags, dev: boolean = import.meta.env.DEV): RouteDef[] {
  return routes.filter((r) => (dev || r.devOnly !== true) && r.hidden?.(flags) !== true);
}

export function routeById(id: ScreenId): RouteDef | undefined {
  return routes.find((r) => r.id === id);
}
