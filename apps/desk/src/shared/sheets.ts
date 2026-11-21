/**
 * Google Sheets source (DESK-012) types. NEM Desk reads the tabs the engine's `sheets`
 * reporter publishes (`<portfolio> summary`, `<portfolio> strategies`, `<portfolio> trades`)
 * from a sheet shared "Anyone with the link: Viewer", as CSV, with no Google account, key or
 * cookie. The sheet ID is private to the user: it stays in main (userData `sheets.json`);
 * the renderer only ever sees a masked form.
 */

import type { SourcesState } from "./data";

export const SHEETS_TABS = ["summary", "strategies", "trades"] as const;
export type SheetsTab = (typeof SHEETS_TABS)[number];

/** Poll choices in minutes; null = only on "Sync now". Default 5, never under 1. */
export const SHEETS_INTERVALS = [1, 5, 15, 30, 60] as const;
export const SHEETS_DEFAULT_INTERVAL = 5;

/** The engine's portfolio name rule (`NAME_PATTERN` in src/nem/core/config.py). */
export const PORTFOLIO_NAME_RE = /^[a-z][a-z0-9_]{0,63}$/;

/** What the renderer sends to save. `sheet` empty keeps the stored sheet. */
export interface SheetsSaveRequest {
  /** A docs.google.com/spreadsheets/d/<id>/... link or the bare ID. */
  sheet: string;
  /** Portfolio names, i.e. the tab prefixes to read. */
  portfolios: string[];
  intervalMinutes: number | null;
}

export type SheetsErrorKind =
  | "not_configured"
  | "invalid"
  /** The sheet is private: Google asked for a sign-in. */
  | "not_shared"
  /** No sheet with that ID (or Google hides it because it isn't shared). */
  | "not_found"
  | "missing_tab"
  /** The tab arrived but failed the contract's schema (details in the data layer). */
  | "schema"
  | "offline"
  | "rate_limited"
  | "upstream"
  | "invalid_response"
  | "too_large"
  | "blocked"
  | "failed";

export interface SheetsError {
  kind: SheetsErrorKind;
  message: string;
  field?: "sheet" | "portfolios" | "intervalMinutes";
  status?: number;
}

export interface SheetsTabResult {
  portfolio: string;
  tab: SheetsTab;
  ok: boolean;
  /** Data rows received (header excluded). */
  rows?: number;
  error?: SheetsError;
}

export interface SheetsSyncInfo {
  at: string;
  ok: boolean;
  tabs: SheetsTabResult[];
  error?: SheetsError;
}

export interface SheetsState {
  configured: boolean;
  /** e.g. `1AbC…9xYz`; never the full ID. */
  sheetMasked: string | null;
  portfolios: string[];
  intervalMinutes: number | null;
  syncing: boolean;
  lastSync: SheetsSyncInfo | null;
  /** When the next automatic poll is due (ISO), null when none is scheduled. */
  nextPollAt: string | null;
}

export type SheetsSaveResult = { ok: true; state: SheetsState } | { ok: false; state: SheetsState; error: SheetsError };

export interface SheetsSyncResult {
  state: SheetsState;
  sync: SheetsSyncInfo;
  sources?: SourcesState;
}

export interface SheetsApi {
  get: () => Promise<SheetsState>;
  save: (req: SheetsSaveRequest) => Promise<SheetsSaveResult>;
  /** Drops the stored sheet, its cached tabs and the "sheets" source. */
  forget: () => Promise<SheetsState>;
  sync: () => Promise<SheetsSyncResult>;
}
