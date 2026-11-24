import { readdirSync, readFileSync, statSync } from "node:fs";
import { basename, dirname, join, relative } from "node:path";
import {
  CONTRACT_MAJOR,
  type BacktestRun,
  type DataError,
  type EquityRow,
  type PortfolioData,
  type StatusReport,
  type StrategyExtras,
  type StrategyRow,
  type SummaryRow,
  type TradeRow,
} from "@shared/data";
import { majorOf, parseTable, versionError } from "./csv";
import { errorField, errorMessage, validator, type SchemaName } from "./schemas";

/**
 * Reads one folder source laid out like the engine's `out/`:
 *
 *   <root>/out/<portfolio>/{summary,strategies,trades}.csv
 *   <root>/out/<portfolio>/equity.csv (contract 1.1, optional: absent from older engines)
 *   <root>/out/backtests/<run_id>.json
 *   <root>/out/summary.json or <root>/summary.json (same for status.json; optional)
 *
 * `root` may be the `out/` folder itself; then the JSON files are looked for in it and in
 * its parent. Every problem becomes a `DataError`; nothing here throws.
 */

export interface LoadedPortfolio extends PortfolioData {
  trades: TradeRow[] | null;
  /** Cell text of trades.csv as written, aligned with `trades` (for CSV export). */
  tradesRaw: string[][];
  tradesHeader: string[];
  /** equity.csv rows; null when the file is absent (older engine) or blocked by an error. */
  equity: EquityRow[] | null;
}

export interface LoadedSource {
  root: string;
  outDir: string;
  portfolios: LoadedPortfolio[];
  extras: Record<string, StrategyExtras>;
  hasSummaryJson: boolean;
  status: StatusReport | null;
  backtests: BacktestRun[];
  errors: DataError[];
  newestAsOf: string | null;
}

const TABLE_FILES = ["summary.csv", "strategies.csv", "trades.csv"] as const;
/** Contract 1.1 table: read when present, never reported missing. */
export const EQUITY_FILE = "equity.csv";

function isDir(p: string): boolean {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
}

function isFile(p: string): boolean {
  try {
    return statSync(p).isFile();
  } catch {
    return false;
  }
}

/** Where the portfolio folders live, and where to look for summary.json / status.json. */
export function resolveLayout(root: string): { outDir: string; jsonDirs: string[] } {
  const out = join(root, "out");
  // `nem report` writes the JSON into out/; older layouts keep it next to out/
  if (isDir(out)) return { outDir: out, jsonDirs: [out, root] };
  return { outDir: root, jsonDirs: [root, dirname(root)] };
}

function readText(path: string, file: string, errors: DataError[]): string | null {
  try {
    return readFileSync(path, "utf8");
  } catch (err) {
    const code = (err as { code?: string }).code;
    errors.push(
      code === "ENOENT"
        ? { kind: "missing_file", file, message: `${file} is missing` }
        : { kind: "unreadable", file, message: `cannot read ${file}: ${String(err)}` },
    );
    return null;
  }
}

/** Parses a contract JSON document: major check first, then the schema. */
// T is the validated document shape the caller expects; the schema check is what earns the cast.
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters
export function parseJsonDoc<T>(text: string, file: string, schema: SchemaName): { doc: T | null; errors: DataError[] } {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (err) {
    return { doc: null, errors: [{ kind: "parse", file, message: `not valid JSON: ${String(err)}` }] };
  }
  const version = (value as { schema_version?: unknown } | null)?.schema_version;
  if (majorOf(version) !== CONTRACT_MAJOR) return { doc: null, errors: [versionError(file, version)] };
  const v = validator(schema);
  if (!v(value)) {
    const errors = (v.errors ?? []).slice(0, 20).map<DataError>((e) => ({
      kind: "invalid",
      file,
      field: errorField(e),
      message: errorMessage(e),
    }));
    return { doc: null, errors };
  }
  return { doc: value as T, errors: [] };
}

function rel(root: string, p: string): string {
  return relative(root, p) || basename(p);
}

export function portfolioDirs(outDir: string): string[] {
  let names: string[];
  try {
    names = readdirSync(outDir);
  } catch {
    return [];
  }
  return names
    .filter((n) => n !== "backtests" && !n.startsWith("."))
    .filter((n) => isDir(join(outDir, n)) && TABLE_FILES.some((f) => isFile(join(outDir, n, f))))
    .sort();
}

export function loadPortfolio(root: string, dir: string): LoadedPortfolio {
  const name = basename(dir);
  const errors: DataError[] = [];
  const read = <T>(file: (typeof TABLE_FILES)[number], schema: SchemaName) => {
    const relFile = rel(root, join(dir, file));
    const text = readText(join(dir, file), relFile, errors);
    if (text === null) return null;
    const t = parseTable<T>(text, relFile, schema);
    errors.push(...t.errors);
    return t;
  };
  const summary = read<SummaryRow>("summary.csv", "summary_row.schema.json");
  const strategies = read<StrategyRow>("strategies.csv", "strategy_row.schema.json");
  const trades = read<TradeRow>("trades.csv", "trade_row.schema.json");
  let equity: EquityRow[] | null = null;
  if (isFile(join(dir, EQUITY_FILE))) {
    const relFile = rel(root, join(dir, EQUITY_FILE));
    const text = readText(join(dir, EQUITY_FILE), relFile, errors);
    if (text !== null) {
      const t = parseTable<EquityRow>(text, relFile, "equity_row.schema.json");
      errors.push(...t.errors);
      equity = t.rows;
    }
  }

  let summaryRow: SummaryRow | null = null;
  if (summary?.rows != null) {
    if (summary.rows.length === 1) summaryRow = summary.rows[0] ?? null;
    else {
      errors.push({
        kind: "invalid",
        file: rel(root, join(dir, "summary.csv")),
        message: `expected exactly one row, found ${summary.rows.length}`,
      });
    }
  }
  return {
    name,
    summary: summaryRow,
    strategies: strategies?.rows ?? null,
    tradeCount: trades?.rows?.length ?? null,
    errors,
    trades: trades?.rows ?? null,
    tradesRaw: trades?.raw ?? [],
    tradesHeader: trades?.header ?? [],
    equity,
  };
}

interface SummaryReportDoc {
  portfolios: { strategies: (StrategyRow & StrategyExtras)[] }[];
}

type BacktestDoc = Omit<BacktestRun, "file">;

function findJson(dirs: string[], file: string): string | null {
  for (const d of dirs) {
    const p = join(d, file);
    if (isFile(p)) return p;
  }
  return null;
}

export function loadSource(root: string): LoadedSource {
  const errors: DataError[] = [];
  const empty: LoadedSource = {
    root,
    outDir: root,
    portfolios: [],
    extras: {},
    hasSummaryJson: false,
    status: null,
    backtests: [],
    errors,
    newestAsOf: null,
  };
  if (!isDir(root)) {
    errors.push({ kind: "source", file: root, message: `source folder not found: ${root}` });
    return empty;
  }
  const { outDir, jsonDirs } = resolveLayout(root);
  const portfolios = portfolioDirs(outDir).map((n) => loadPortfolio(root, join(outDir, n)));

  const extras: Record<string, StrategyExtras> = {};
  let hasSummaryJson = false;
  const summaryPath = findJson(jsonDirs, "summary.json");
  if (summaryPath !== null) {
    const file = rel(root, summaryPath);
    const text = readText(summaryPath, file, errors);
    if (text !== null) {
      const r = parseJsonDoc<SummaryReportDoc>(text, file, "summary_report.schema.json");
      errors.push(...r.errors);
      if (r.doc !== null) {
        hasSummaryJson = true;
        for (const p of r.doc.portfolios) {
          for (const s of p.strategies) {
            extras[`${s.portfolio}/${s.strategy}`] = { signal_reasons: s.signal_reasons, calibration: s.calibration };
          }
        }
      }
    }
  }

  let status: StatusReport | null = null;
  const statusPath = findJson(jsonDirs, "status.json");
  if (statusPath !== null) {
    const file = rel(root, statusPath);
    const text = readText(statusPath, file, errors);
    if (text !== null) {
      const r = parseJsonDoc<StatusReport>(text, file, "status.schema.json");
      errors.push(...r.errors);
      status = r.doc;
    }
  }

  const backtests: BacktestRun[] = [];
  const btDir = join(outDir, "backtests");
  if (isDir(btDir)) {
    let names: string[] = [];
    try {
      names = readdirSync(btDir).filter((n) => n.endsWith(".json")).sort();
    } catch (err) {
      errors.push({ kind: "unreadable", file: rel(root, btDir), message: String(err) });
    }
    for (const n of names) {
      const file = rel(root, join(btDir, n));
      const text = readText(join(btDir, n), file, errors);
      if (text === null) continue;
      const r = parseJsonDoc<BacktestDoc>(text, file, "backtest_run.schema.json");
      errors.push(...r.errors);
      if (r.doc !== null) backtests.push({ ...r.doc, file });
    }
  }

  const stamps = [
    ...portfolios.map((p) => p.summary?.as_of ?? null),
    status?.as_of ?? null,
  ].filter((s): s is string => s !== null);
  // Contract timestamps are fixed-format UTC with Z, so they sort as text.
  const newestAsOf = stamps.length > 0 ? stamps.reduce((a, b) => (b > a ? b : a)) : null;

  return { root, outDir, portfolios, extras, hasSummaryJson, status, backtests, errors, newestAsOf };
}

/** Cheap fingerprint of every file a load reads (mtime + size), to know when to reload. */
export function fingerprint(root: string): string {
  const parts: string[] = [];
  const add = (p: string): void => {
    try {
      const s = statSync(p);
      parts.push(`${p}:${s.mtimeMs}:${s.size}`);
    } catch {
      parts.push(`${p}:-`);
    }
  };
  if (!isDir(root)) return "missing";
  const { outDir, jsonDirs } = resolveLayout(root);
  add(outDir);
  for (const d of jsonDirs) {
    add(join(d, "summary.json"));
    add(join(d, "status.json"));
  }
  for (const n of portfolioDirs(outDir)) {
    for (const f of [...TABLE_FILES, EQUITY_FILE]) add(join(outDir, n, f));
  }
  const bt = join(outDir, "backtests");
  add(bt);
  try {
    for (const n of readdirSync(bt)) add(join(bt, n));
  } catch {
    // no backtests folder
  }
  return parts.join("|");
}
