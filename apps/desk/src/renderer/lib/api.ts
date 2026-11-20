import type { NemApi } from "@shared/ipc";

/**
 * The renderer's only door to the main process. Outside Electron (unit tests, a plain
 * browser) `window.nem` is missing and this returns an offline stub, so screens render
 * their empty states instead of crashing.
 */
const offline: NemApi = {
  appVersion: () => Promise.resolve("dev"),
  listSources: () => Promise.resolve([]),
  platform: typeof navigator !== "undefined" ? navigator.platform : "",
};

export function nem(): NemApi {
  return (typeof window !== "undefined" ? window.nem : undefined) ?? offline;
}

/** TanStack Query keys, centralized so `r` (refresh) can invalidate everything. */
export const queryKeys = {
  appVersion: ["app", "version"] as const,
  sources: ["data", "sources"] as const,
};
