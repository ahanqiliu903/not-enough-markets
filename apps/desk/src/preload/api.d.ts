import type { NemApi } from "../shared/ipc";

declare global {
  interface Window {
    /** Present inside Electron; undefined in plain browser/test environments. */
    nem?: NemApi;
  }
}

export {};
