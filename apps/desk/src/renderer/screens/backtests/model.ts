import type { BacktestRun, BacktestVariant, Snapshot, StrategyRow } from "@shared/data";

/**
 * Layout helpers for the Backtests screen. Every number shown is the engine's (`nem suggest
 * --save`); this only places variants on the grid and picks display buckets.
 */

export type HeatBucket = "up-1" | "up-2" | "up-3" | "down-1" | "down-2" | "down-3" | "zero" | "none";

export interface HeatCell {
  variant: BacktestVariant | null;
  /** Train edge, cents per contract (engine). */
  edge: number | null;
  /** Train settled trades (engine). */
  n: number | null;
  chosen: boolean;
  /** Fewer train trades than the run's `min_trades`: the engine did not consider it. */
  muted: boolean;
  bucket: HeatBucket;
}

export interface Heatmap {
  /** Axis drawn as rows (null for a one-axis grid: a single row). */
  rowAxis: { path: string; values: unknown[] } | null;
  colAxis: { path: string; values: unknown[] };
  cells: HeatCell[][];
}

/** Color bucket by sign and size relative to the largest |edge| among eligible cells. */
export function heatBucket(edge: number | null, maxAbs: number): HeatBucket {
  if (edge === null || !Number.isFinite(edge)) return "none";
  if (edge === 0 || maxAbs <= 0) return "zero";
  const rel = Math.abs(edge) / maxAbs;
  const level = rel > 2 / 3 ? 3 : rel > 1 / 3 ? 2 : 1;
  return `${edge > 0 ? "up" : "down"}-${level}` as HeatBucket;
}

/** The sweep as a 1- or 2-axis grid of train edges; null for 3+ axes (table only). */
export function buildHeatmap(run: BacktestRun): Heatmap | null {
  const axes = run.grid;
  const [first, second] = axes;
  if (first === undefined || axes.length > 2) return null;
  const rowAxis = second === undefined ? null : first;
  const colAxis = second ?? first;
  const rows = rowAxis === null ? 1 : rowAxis.values.length;
  const byIndex = new Map<string, BacktestVariant>();
  for (const v of run.variants) byIndex.set(v.index.join(","), v);
  const eligible = run.variants
    .filter((v) => v.train.trades >= run.min_trades && v.train.edge_cents !== null)
    .map((v) => Math.abs(v.train.edge_cents ?? 0));
  const maxAbs = eligible.length > 0 ? Math.max(...eligible) : 0;
  const cells: HeatCell[][] = [];
  for (let r = 0; r < rows; r++) {
    const row: HeatCell[] = [];
    for (let c = 0; c < colAxis.values.length; c++) {
      const v = byIndex.get(rowAxis === null ? `${c}` : `${r},${c}`) ?? null;
      const muted = v !== null && v.train.trades < run.min_trades;
      const edge = v?.train.edge_cents ?? null;
      row.push({
        variant: v,
        edge,
        n: v?.train.trades ?? null,
        chosen: v !== null && v.name === run.chosen,
        muted,
        bucket: muted ? "none" : heatBucket(edge, maxAbs),
      });
    }
    cells.push(row);
  }
  return { rowAxis, colAxis, cells };
}

export function chosenVariant(run: BacktestRun): BacktestVariant | null {
  if (run.chosen === null) return null;
  return run.variants.find((v) => v.name === run.chosen) ?? null;
}

/** Grid values are arbitrary JSON; show numbers and strings plainly, the rest as JSON. */
export function showValue(v: unknown): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (v === null || v === undefined) return "null";
  return JSON.stringify(v);
}

/** The paper strategy row (strategies.csv) for the run's portfolio/strategy, if exported. */
export function paperRow(s: Snapshot, run: BacktestRun): StrategyRow | null {
  const p = s.portfolios.find((x) => x.name === run.portfolio);
  return p?.strategies?.find((r) => r.strategy === run.strategy) ?? null;
}

/**
 * True when both edge intervals exist and do not overlap: a plain comparison of the
 * engine's interval ends, used only to flag a large divergence. No statistic is derived.
 */
export function intervalsDisjoint(
  a: { lo: number | null; hi: number | null },
  b: { lo: number | null; hi: number | null },
): boolean {
  if (a.lo === null || a.hi === null || b.lo === null || b.hi === null) return false;
  return a.hi < b.lo || b.hi < a.lo;
}

/** Position of [start, end) windows on a shared time line, as fractions 0..1. */
export function windowSpans(run: BacktestRun): { train: [number, number]; test: [number, number] } | null {
  const t = [run.train_window.start, run.train_window.end, run.test_window.start, run.test_window.end].map((x) =>
    Date.parse(x),
  );
  if (t.some((x) => !Number.isFinite(x))) return null;
  const lo = Math.min(...t);
  const hi = Math.max(...t);
  const span = hi - lo || 1;
  const f = (x: number) => (x - lo) / span;
  const [a = 0, b = 0, c = 0, d = 0] = t;
  return { train: [f(a), f(b)], test: [f(c), f(d)] };
}
