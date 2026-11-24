import { mkdirSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { parse } from "csv-parse/sync";
import {
  PORTFOLIO_NAME_RE,
  SHEETS_DEFAULT_INTERVAL,
  SHEETS_INTERVALS,
  OPTIONAL_SHEETS_TABS,
  SHEETS_TABS,
  type SheetsError,
  type SheetsSaveResult,
  type SheetsState,
  type SheetsSyncInfo,
  type SheetsTab,
  type SheetsTabResult,
} from "@shared/sheets";
import { parseTable } from "../data/csv";
import { tableSchema, type SchemaName } from "../data/schemas";
import { maskId } from "../log/redact";
import { parseSheetRef, type SheetsClient } from "./client";

/**
 * Google Sheets source (DESK-012). Each sync fetches `<portfolio> summary|strategies|trades`
 * (and `equity` when the engine wrote it: contract 1.1, optional)
 * as CSV, checks Google really returned that tab, and writes it to
 * `<cacheDir>/out/<portfolio>/<tab>.csv`. The cache is then an ordinary folder source, so the
 * data layer applies exactly the same coercion and schema validation as to the engine's own
 * CSVs (and the same watching, paging and error display). The sheet ID lives only in
 * `storePath` (userData, mode 0600) and memory.
 */

const SCHEMA: Record<SheetsTab, SchemaName> = {
  summary: "summary_row.schema.json",
  strategies: "strategy_row.schema.json",
  trades: "trade_row.schema.json",
  equity: "equity_row.schema.json",
};

export const MAX_PORTFOLIOS = 20;
/** Longest wait between polls after repeated quota / server errors. */
export const MAX_BACKOFF_MS = 60 * 60_000;

export interface SheetsConfig {
  sheetId: string;
  portfolios: string[];
  intervalMinutes: number | null;
}

interface Stored {
  version: 1;
  config: SheetsConfig | null;
  lastSync: SheetsSyncInfo | null;
}

function invalid(field: SheetsError["field"], message: string): { ok: false; error: SheetsError } {
  return { ok: false, error: { kind: "invalid", ...(field !== undefined ? { field } : {}), message } };
}

export function parsePortfolios(raw: unknown): { ok: true; names: string[] } | { ok: false; error: SheetsError } {
  const list = Array.isArray(raw) ? raw : typeof raw === "string" ? raw.split(/[\s,]+/) : [];
  const names = [...new Set(list.filter((x): x is string => typeof x === "string").map((x) => x.trim()).filter((x) => x !== ""))];
  if (names.length === 0) return invalid("portfolios", "Name at least one portfolio (the tab prefix, e.g. btc for \"btc summary\").");
  if (names.length > MAX_PORTFOLIOS) return invalid("portfolios", `At most ${MAX_PORTFOLIOS} portfolios.`);
  const bad = names.find((n) => !PORTFOLIO_NAME_RE.test(n) || n === "backtests");
  if (bad !== undefined) return invalid("portfolios", `"${bad.slice(0, 40)}" is not a portfolio name (lowercase letters, digits, _; starts with a letter).`);
  return { ok: true, names };
}

export function validateInterval(raw: unknown): { ok: true; minutes: number | null } | { ok: false; error: SheetsError } {
  if (raw === undefined) return { ok: true, minutes: SHEETS_DEFAULT_INTERVAL };
  if (raw === null) return { ok: true, minutes: null };
  if ((SHEETS_INTERVALS as readonly unknown[]).includes(raw)) return { ok: true, minutes: raw as number };
  return invalid("intervalMinutes", `Auto refresh: off or one of ${SHEETS_INTERVALS.join(", ")} minutes.`);
}

function csvCell(s: string): string {
  return /[",\r\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

/**
 * gviz pads a tab with empty columns and rows up to the sheet's grid size, and quotes every
 * cell. Drop columns with an empty header and no values, and rows with no values; cell
 * text is untouched (coercion happens in the data layer, as for any CSV).
 */
export function normalizeGvizCsv(text: string): { ok: true; records: string[][] } | { ok: false; error: SheetsError } {
  let records: string[][];
  try {
    records = parse(text, { bom: true, skip_empty_lines: true, relax_column_count: true });
  } catch (e) {
    return { ok: false, error: { kind: "invalid_response", message: `Google sent CSV that does not parse (${String(e).slice(0, 200)}).` } };
  }
  const header = records[0] ?? [];
  const keep = header.map((h, c) => h !== "" || records.some((r, i) => i > 0 && (r[c] ?? "") !== ""));
  const rows = records
    .map((r) => r.filter((_, c) => keep[c] ?? true))
    .filter((r, i) => i === 0 || r.some((v) => v !== ""));
  return { ok: true, records: rows };
}

export function toCsv(records: string[][]): string {
  return records.length === 0 ? "" : `${records.map((r) => r.map(csvCell).join(",")).join("\n")}\n`;
}

/**
 * gviz answers an unknown `sheet=` name with the *first* tab instead of an error, so a
 * missing tab looks like a valid one. Catch it: the header must have this tab's columns,
 * and every row must belong to the requested portfolio.
 */
export function checkTab(records: string[][], portfolio: string, tab: SheetsTab): SheetsError | null {
  const header = records[0] ?? [];
  const title = `${portfolio} ${tab}`;
  const missing: SheetsError = {
    kind: "missing_tab",
    message: `No tab named "${title}" (Google returned a different tab). Run nem report with the sheets reporter, or check the portfolio name.`,
  };
  if (header.length === 0) return { kind: "missing_tab", message: `Tab "${title}" is empty (no header row).` };
  const fits = (t: SheetsTab) => tableSchema(SCHEMA[t]).required.every((c) => header.includes(c));
  if (!fits(tab)) {
    if (SHEETS_TABS.some((t) => t !== tab && fits(t))) return missing;
    if (!header.includes("schema_version")) return missing;
    return null; // our tab with a column problem: the schema check reports it
  }
  const col = header.indexOf("portfolio");
  if (col >= 0 && records.slice(1).some((r) => (r[col] ?? "") !== portfolio)) return missing;
  return null;
}

function writeAtomic(path: string, text: string): void {
  mkdirSync(dirname(path), { recursive: true });
  const tmp = `${path}.tmp`;
  writeFileSync(tmp, text);
  renameSync(tmp, path);
}

export interface SheetsManagerOptions {
  storePath: string;
  cacheDir: string;
  client: SheetsClient;
  /** After a sync that wrote anything: add or refresh the cache as a source. */
  onSynced?: (cacheDir: string, reason: "manual" | "poll") => void;
  onForget?: (cacheDir: string) => void;
  log?: (level: "info" | "warn", message: string) => void;
  now?: () => Date;
  /** Turn off the poll timer (tests). */
  timers?: boolean;
  /** First automatic poll after start (default 5 s). */
  firstPollMs?: number;
}

const STOPS_SYNC: readonly SheetsError["kind"][] = ["not_shared", "not_found", "offline", "rate_limited", "upstream", "blocked"];
const BACKS_OFF: readonly SheetsError["kind"][] = ["rate_limited", "upstream", "offline"];

export class SheetsManager {
  private config: SheetsConfig | null = null;
  private lastSync: SheetsSyncInfo | null = null;
  private running: Promise<SheetsSyncInfo> | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private nextAt: number | null = null;
  private failures = 0;

  constructor(private readonly opts: SheetsManagerOptions) {
    try {
      const raw = JSON.parse(readFileSync(opts.storePath, "utf8")) as Partial<Stored>;
      const c = raw.config;
      if (c !== null && c !== undefined) {
        const ref = parseSheetRef(c.sheetId);
        const p = parsePortfolios(c.portfolios);
        const i = validateInterval(c.intervalMinutes);
        if (ref.ok && p.ok && i.ok) this.config = { sheetId: ref.id, portfolios: p.names, intervalMinutes: i.minutes };
      }
      this.lastSync = raw.lastSync ?? null;
    } catch {
      // first run
    }
    this.schedule(opts.firstPollMs ?? 5000);
  }

  /** The private values to redact from logs and diagnostics. */
  sheetId(): string | null {
    return this.config?.sheetId ?? null;
  }

  state(): SheetsState {
    return {
      configured: this.config !== null,
      sheetMasked: this.config === null ? null : maskId(this.config.sheetId),
      portfolios: this.config?.portfolios ?? [],
      intervalMinutes: this.config?.intervalMinutes ?? SHEETS_DEFAULT_INTERVAL,
      syncing: this.running !== null,
      lastSync: this.lastSync,
      nextPollAt: this.nextAt === null ? null : new Date(this.nextAt).toISOString(),
    };
  }

  private write(): void {
    const data: Stored = { version: 1, config: this.config, lastSync: this.lastSync };
    try {
      mkdirSync(dirname(this.opts.storePath), { recursive: true });
      const tmp = `${this.opts.storePath}.tmp`;
      writeFileSync(tmp, JSON.stringify(data, null, 2), { mode: 0o600 });
      renameSync(tmp, this.opts.storePath);
    } catch {
      // not persisting is survivable
    }
  }

  save(raw: unknown): SheetsSaveResult {
    const r = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
    const sheet = typeof r["sheet"] === "string" ? r["sheet"].trim() : "";
    let id: string;
    if (sheet === "" && this.config !== null) id = this.config.sheetId;
    else {
      const ref = parseSheetRef(sheet);
      if (!ref.ok) return { ok: false, state: this.state(), error: ref.error };
      id = ref.id;
    }
    const p = parsePortfolios(r["portfolios"]);
    if (!p.ok) return { ok: false, state: this.state(), error: p.error };
    const i = validateInterval(r["intervalMinutes"]);
    if (!i.ok) return { ok: false, state: this.state(), error: i.error };
    const changedSheet = this.config?.sheetId !== id;
    this.config = { sheetId: id, portfolios: p.names, intervalMinutes: i.minutes };
    if (changedSheet) this.lastSync = null;
    this.failures = 0;
    this.write();
    this.opts.log?.("info", `saved: ${p.names.length} portfolio(s), auto refresh ${i.minutes === null ? "off" : `${i.minutes} min`}`);
    this.schedule();
    return { ok: true, state: this.state() };
  }

  forget(): SheetsState {
    this.config = null;
    this.lastSync = null;
    this.failures = 0;
    this.write();
    this.schedule();
    try {
      rmSync(this.opts.cacheDir, { recursive: true, force: true });
    } catch {
      // a leftover cache is only a copy of what the sheet showed
    }
    this.opts.onForget?.(this.opts.cacheDir);
    this.opts.log?.("info", "forgot the sheet and removed its cache");
    return this.state();
  }

  private schedule(delayMs?: number): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.nextAt = null;
    const m = this.config?.intervalMinutes ?? null;
    if (this.config === null || m === null || this.opts.timers === false) return;
    const base = m * 60_000;
    const wait = delayMs ?? Math.min(base * 2 ** Math.min(this.failures, 6), Math.max(base, MAX_BACKOFF_MS));
    this.nextAt = Date.now() + wait;
    this.timer = setTimeout(() => {
      void this.sync("poll").finally(() => this.schedule());
    }, wait);
    this.timer.unref();
  }

  /** One pass over every configured tab; concurrent calls share the run in progress. */
  sync(reason: "manual" | "poll" = "manual"): Promise<SheetsSyncInfo> {
    this.running ??= this.doSync(reason).finally(() => {
      this.running = null;
    });
    return this.running;
  }

  private pruneCache(keep: string[]): void {
    const out = join(this.opts.cacheDir, "out");
    let names: string[];
    try {
      names = readdirSync(out);
    } catch {
      return;
    }
    for (const n of names) {
      if (PORTFOLIO_NAME_RE.test(n) && !keep.includes(n)) rmSync(join(out, n), { recursive: true, force: true });
    }
  }

  private async doSync(reason: "manual" | "poll"): Promise<SheetsSyncInfo> {
    const at = (this.opts.now?.() ?? new Date()).toISOString();
    const c = this.config;
    if (c === null) {
      return { at, ok: false, tabs: [], error: { kind: "not_configured", message: "Paste the sheet's link and name a portfolio first." } };
    }
    const tabs: SheetsTabResult[] = [];
    let stop: SheetsError | undefined;
    let wrote = false;
    for (const portfolio of c.portfolios) {
      for (const tab of SHEETS_TABS) {
        if (stop !== undefined) break;
        const title = `${portfolio} ${tab}`;
        const file = join(this.opts.cacheDir, "out", portfolio, `${tab}.csv`);
        const r = await this.opts.client.tab(c.sheetId, title);
        if (!r.ok) {
          tabs.push({ portfolio, tab, ok: false, error: r.error });
          if (STOPS_SYNC.includes(r.error.kind)) stop = r.error;
          continue;
        }
        const norm = normalizeGvizCsv(r.text);
        if (!norm.ok) {
          tabs.push({ portfolio, tab, ok: false, error: norm.error });
          continue;
        }
        const wrong = checkTab(norm.records, portfolio, tab);
        if (wrong !== null) {
          // The tab is gone: drop the stale copy so the data layer reports it missing.
          rmSync(file, { force: true });
          wrote = true;
          if (OPTIONAL_SHEETS_TABS.includes(tab)) continue; // older engine: no chart, no error
          tabs.push({ portfolio, tab, ok: false, error: wrong });
          continue;
        }
        const text = toCsv(norm.records);
        const rows = Math.max(0, norm.records.length - 1);
        try {
          writeAtomic(file, text);
          wrote = true;
        } catch (e) {
          tabs.push({ portfolio, tab, ok: false, rows, error: { kind: "failed", message: `Cannot write the cache: ${String(e)}` } });
          continue;
        }
        // Same reader as folder sources, run here only to report the outcome per tab.
        const t = parseTable(text, `${portfolio}/${tab}.csv`, SCHEMA[tab]);
        const first = t.errors[0];
        if (first !== undefined) {
          const more = t.errors.length > 1 ? ` (+${t.errors.length - 1} more)` : "";
          tabs.push({ portfolio, tab, ok: false, rows, error: { kind: "schema", message: `${first.message}${more}` } });
        } else {
          tabs.push({ portfolio, tab, ok: true, rows });
        }
      }
    }
    if (stop === undefined) this.pruneCache(c.portfolios);
    this.failures = stop !== undefined && BACKS_OFF.includes(stop.kind) ? this.failures + 1 : 0;
    const firstError = stop ?? tabs.find((t) => !t.ok)?.error;
    const info: SheetsSyncInfo = { at, ok: firstError === undefined, tabs, ...(firstError !== undefined ? { error: firstError } : {}) };
    // A config change while fetching makes this result stale; don't record it.
    if (this.config === c) {
      this.lastSync = info;
      this.write();
      if (wrote) this.opts.onSynced?.(this.opts.cacheDir, reason);
    }
    const okTabs = tabs.filter((t) => t.ok).length;
    this.opts.log?.(
      info.ok ? "info" : "warn",
      `${reason} sync: ${okTabs}/${tabs.length} tab(s) ok${firstError !== undefined ? `; ${firstError.kind}: ${firstError.message}` : ""}`,
    );
    return info;
  }

  dispose(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
}
