/**
 * Logs and diagnostics (DESK-020). Everything here is built and redacted in main: sheet
 * IDs, hostnames, user names, the home folder and key paths are replaced with placeholders
 * such as `<sheet-id>`, `<host>`, `<user>`, `~` and `<key-path>` before the renderer sees it.
 */

export interface DiagSource {
  id: string;
  label: string;
  kind: string;
  path: string;
  active: boolean;
  db: string | null;
  /** Last time the data layer (re)read this source, and how many problems it found. */
  lastLoadAt: string | null;
  errorCount: number | null;
  errors: string[];
}

export interface Diagnostics {
  generatedAt: string;
  app: { version: string; electron: string; chrome: string; node: string; platform: string; arch: string; packaged: boolean };
  contract: { expected: string; dbSchemaVersions: number[] };
  userData: string;
  logFile: string;
  sources: DiagSource[];
  db: { path: string; schemaVersion: number; sizeBytes: number; series: number; feeds: number; newestTs: string | null } | { error: string } | null;
  sheets: { configured: boolean; portfolios: string[]; intervalMinutes: number | null; lastSync: string | null };
  vps: { configured: boolean; intervalMinutes: number | null; lastSync: string | null };
  /** Newest last. */
  recentLog: string[];
}

export type DiagCopyResult = { ok: true; chars: number } | { ok: false; message: string };

export interface DiagnosticsApi {
  get: () => Promise<Diagnostics>;
  /** Main puts the redacted text report on the clipboard. */
  copy: () => Promise<DiagCopyResult>;
  /** Shows the log file in Finder / Explorer. */
  revealLog: () => Promise<boolean>;
}
