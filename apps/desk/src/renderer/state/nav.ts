import { create } from "zustand";
import type { ScreenId } from "../routes";

/** Flags the data layer sets to reveal screens that only make sense with data. */
export interface RouteFlags {
  hasBacktests: boolean;
}

export interface NavEntry {
  screen: ScreenId;
  /** Screen-specific parameters, e.g. { portfolio: "demo", strategy: "btc15" }. */
  params: Readonly<Record<string, string>>;
}

export interface NavState extends NavEntry {
  history: NavEntry[];
  paletteOpen: boolean;
  flags: RouteFlags;
  navigate: (screen: ScreenId, params?: Record<string, string>) => void;
  back: () => boolean;
  setPaletteOpen: (open: boolean) => void;
  setFlags: (flags: Partial<RouteFlags>) => void;
}

const MAX_HISTORY = 50;

export const useNav = create<NavState>()((set, get) => ({
  screen: "portfolios",
  params: {},
  history: [],
  paletteOpen: false,
  flags: { hasBacktests: false },
  navigate: (screen, params = {}) => {
    const { screen: cur, params: curParams, history } = get();
    if (cur === screen && JSON.stringify(curParams) === JSON.stringify(params)) return;
    set({
      screen,
      params,
      history: [...history, { screen: cur, params: curParams }].slice(-MAX_HISTORY),
    });
  },
  back: () => {
    const { history } = get();
    const prev = history.at(-1);
    if (prev === undefined) return false;
    set({ ...prev, history: history.slice(0, -1) });
    return true;
  },
  setPaletteOpen: (open) => set({ paletteOpen: open }),
  setFlags: (flags) => set({ flags: { ...get().flags, ...flags } }),
}));
