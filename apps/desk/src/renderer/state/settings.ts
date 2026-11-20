import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";
import { FONT_SIZE_DEFAULT, FONT_SIZE_MAX, FONT_SIZE_MIN } from "../theme";

export interface SettingsState {
  pixelAccents: boolean;
  fontSize: number;
  navWidth: number;
  setPixelAccents: (on: boolean) => void;
  setFontSize: (px: number) => void;
  setNavWidth: (px: number) => void;
}

export const NAV_WIDTH_MIN = 140;
export const NAV_WIDTH_MAX = 420;

export function clampFontSize(px: number): number {
  if (!Number.isFinite(px)) return FONT_SIZE_DEFAULT;
  return Math.min(FONT_SIZE_MAX, Math.max(FONT_SIZE_MIN, Math.round(px)));
}

function clampNav(px: number): number {
  if (!Number.isFinite(px)) return 232;
  return Math.min(NAV_WIDTH_MAX, Math.max(NAV_WIDTH_MIN, Math.round(px)));
}

/** localStorage can throw (blocked storage, private mode); settings then just don't persist. */
const safeStorage: StateStorage = {
  getItem: (key) => {
    try {
      return globalThis.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  setItem: (key, value) => {
    try {
      globalThis.localStorage.setItem(key, value);
    } catch {
      // ignore
    }
  },
  removeItem: (key) => {
    try {
      globalThis.localStorage.removeItem(key);
    } catch {
      // ignore
    }
  },
};

export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      pixelAccents: true,
      fontSize: FONT_SIZE_DEFAULT,
      navWidth: 232,
      setPixelAccents: (on) => set({ pixelAccents: on }),
      setFontSize: (px) => set({ fontSize: clampFontSize(px) }),
      setNavWidth: (px) => set({ navWidth: clampNav(px) }),
    }),
    {
      name: "nem-desk.settings",
      version: 1,
      storage: createJSONStorage(() => safeStorage),
      partialize: (s) => ({ pixelAccents: s.pixelAccents, fontSize: s.fontSize, navWidth: s.navWidth }),
      merge: (persisted, current) => {
        const p = (persisted ?? {}) as Partial<SettingsState>;
        return {
          ...current,
          pixelAccents: typeof p.pixelAccents === "boolean" ? p.pixelAccents : current.pixelAccents,
          fontSize: clampFontSize(p.fontSize ?? current.fontSize),
          navWidth: clampNav(p.navWidth ?? current.navWidth),
        };
      },
    },
  ),
);
