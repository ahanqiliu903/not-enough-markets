/**
 * VPS sync (DESK-013) types. NEM Desk pulls the server's `data/snapshot/nem.db` and `out/`
 * over the system `ssh` + `rsync` into a cache in its own userData folder. Only the host,
 * user, port, key *path* and remote folder are stored; never a password or key contents.
 */

import type { SourcesState } from "./data";
import type { HaltApi } from "./halt";

export const DEFAULT_REMOTE_BASE = "/home/nem/not-enough-markets";

export interface VpsConfig {
  host: string;
  user: string;
  port: number;
  /** Absolute path to a private key file on this machine (its contents are never read). */
  keyPath: string;
  /** The repo checkout on the server (holds `data/snapshot/nem.db` and `out/`). */
  remoteBase: string;
  /** Pull automatically every N minutes while the app runs; null = only on "Sync now". */
  intervalMinutes: number | null;
}

export type VpsErrorKind =
  | "not_configured"
  | "invalid"
  /** `ssh` or `rsync` is not installed. */
  | "missing_tool"
  /** The host key is unknown or changed: fail closed, the user must verify it in a terminal. */
  | "host_key"
  | "auth"
  | "unreachable"
  | "remote_missing"
  | "timeout"
  | "busy"
  | "failed";

export interface VpsError {
  kind: VpsErrorKind;
  message: string;
  /** Which config field is wrong (kind "invalid"). */
  field?: keyof VpsConfig;
  /** Last lines of the tool's stderr, for diagnosis. */
  detail?: string;
}

export interface VpsStep {
  what: "out" | "db";
  ok: boolean;
  error?: VpsError;
}

export interface VpsSyncInfo {
  at: string;
  ok: boolean;
  steps: VpsStep[];
  /** A summary error when the sync as a whole failed. */
  error?: VpsError;
}

export interface VpsState {
  config: VpsConfig | null;
  /** Local cache folder (inside userData); added as the "VPS" source after a sync. */
  cacheDir: string;
  syncing: boolean;
  lastSync: VpsSyncInfo | null;
}

export type VpsSaveResult = { ok: true; state: VpsState } | { ok: false; state: VpsState; error: VpsError };

export interface VpsSyncResult {
  state: VpsState;
  sync: VpsSyncInfo;
  /** The sources list after the cache was added or refreshed as a source. */
  sources?: SourcesState;
}

export interface VpsApi {
  get: () => Promise<VpsState>;
  save: (config: VpsConfig) => Promise<VpsSaveResult>;
  forget: () => Promise<VpsState>;
  sync: () => Promise<VpsSyncResult>;
  /** Shows a file picker (default ~/.ssh); returns the chosen path or null. */
  pickKey: () => Promise<string | null>;
  /** DESK-021: `nem halt` / `nem resume` on this VPS over ssh (off by default). Optional so older test doubles still fit; missing means off. */
  halt?: HaltApi;
}
