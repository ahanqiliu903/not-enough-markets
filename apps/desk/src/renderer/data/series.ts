import type { PnlCurve } from "@shared/data";

/**
 * Chart-only transforms of cumulative P&L curves (running sums of trades.csv, which the
 * contract allows). No statistic is derived here; the engine's numbers stay the source.
 */

/** Distance below the running peak (<= 0), starting from a peak of 0 before any trade. */
export function drawdownSeries(pnl: readonly number[]): number[] {
  let peak = 0;
  return pnl.map((v) => {
    peak = Math.max(peak, v);
    return Math.round((v - peak) * 10000) / 10000;
  });
}

export interface Aligned {
  x: number[];
  ys: (number | null)[][];
}

/** x = trade number; every curve starts at 0 at trade 0 and ends at its own last trade. */
export function alignByTrade(curves: readonly PnlCurve[]): Aligned {
  const n = Math.max(0, ...curves.map((c) => c.pnl.length));
  const x = Array.from({ length: n + 1 }, (_, i) => i);
  const ys = curves.map((c) => x.map((i) => (i === 0 ? 0 : i <= c.pnl.length ? (c.pnl[i - 1] ?? null) : null)));
  return { x, ys };
}

/**
 * x = time. All curves start at 0 at the earliest first settlement among them (the common
 * start) and step at each settlement; the value holds between settlements.
 */
export function alignByTime(curves: readonly PnlCurve[]): Aligned {
  const times = [...new Set(curves.flatMap((c) => c.t))].sort((a, b) => a - b);
  if (times.length === 0) return { x: [], ys: curves.map(() => []) };
  const start = times[0] ?? 0;
  const x = [start - 1, ...times];
  const ys = curves.map((c) => {
    const out: (number | null)[] = [0];
    let j = 0;
    let last = 0;
    for (const t of times) {
      while (j < c.t.length && (c.t[j] ?? Infinity) <= t) {
        last = c.pnl[j] ?? last;
        j++;
      }
      out.push(last);
    }
    return out;
  });
  return { x, ys };
}
