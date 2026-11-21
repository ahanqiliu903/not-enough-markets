import { create } from "zustand";

/** Strategies picked for the Compare screen, as "portfolio/strategy" ids (up to 4). */
export const COMPARE_MAX = 4;

interface CompareState {
  ids: string[];
  toggle: (id: string) => void;
  add: (id: string) => void;
  remove: (id: string) => void;
  clear: () => void;
}

export const useCompare = create<CompareState>()((set, get) => ({
  ids: [],
  toggle: (id) => {
    if (get().ids.includes(id)) get().remove(id);
    else get().add(id);
  },
  add: (id) => {
    const ids = get().ids;
    if (ids.includes(id)) return;
    // Over the limit, the oldest pick drops out.
    set({ ids: [...ids, id].slice(-COMPARE_MAX) });
  },
  remove: (id) => set({ ids: get().ids.filter((x) => x !== id) }),
  clear: () => set({ ids: [] }),
}));
