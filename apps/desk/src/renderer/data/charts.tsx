import { useEffect, useRef } from "react";
import type uPlot from "uplot";
import { chartSeriesColors, colors, derivedColors } from "../theme";
import "uplot/dist/uPlot.min.css";

/**
 * Charts. SVG for the small interval / progress / calibration views; uPlot (canvas) for
 * P&L curves, which can have tens of thousands of points. Every value drawn is the
 * engine's (or a running sum of trades.csv for the curves); colors come from theme tokens.
 */

const W = 400;

function scale(min: number, max: number): (v: number) => number {
  const span = max - min || 1;
  return (v) => 8 + ((Math.min(max, Math.max(min, v)) - min) / span) * (W - 16);
}

/**
 * An estimate with its 95% interval on a line, plus a reference mark (break-even, zero).
 * Renders nothing but a note when the estimate is missing.
 */
export function IntervalChart({
  label,
  value,
  lo,
  hi,
  reference,
  referenceLabel,
  domain,
  format,
}: {
  label: string;
  value: number | null;
  lo: number | null;
  hi: number | null;
  reference: number | null;
  referenceLabel: string;
  domain: [number, number];
  format: (v: number) => string;
}) {
  if (value === null) {
    return <p className="dim">{label}: no settled trades yet.</p>;
  }
  const x = scale(domain[0], domain[1]);
  const above = reference !== null && lo !== null && lo > reference;
  const below = reference !== null && hi !== null && hi < reference;
  const tone = above ? "chart-up" : below ? "chart-down" : "chart-mid";
  return (
    <svg className="chart-interval" viewBox={`0 0 ${W} 48`} role="img" aria-label={label} preserveAspectRatio="none">
      <line className="chart-axis" x1={8} x2={W - 8} y1={30} y2={30} />
      <text className="chart-tick" x={8} y={46} textAnchor="start">
        {format(domain[0])}
      </text>
      <text className="chart-tick" x={W - 8} y={46} textAnchor="end">
        {format(domain[1])}
      </text>
      {lo !== null && hi !== null && (
        <rect className={`chart-band ${tone}`} x={x(lo)} y={24} width={Math.max(1, x(hi) - x(lo))} height={12} />
      )}
      <circle className={`chart-point ${tone}`} cx={x(value)} cy={30} r={4} />
      {reference !== null && (
        <>
          <line className="chart-ref" x1={x(reference)} x2={x(reference)} y1={14} y2={42} />
          <text className="chart-ref-label" x={x(reference)} y={11} textAnchor="middle">
            {referenceLabel} {format(reference)}
          </text>
        </>
      )}
    </svg>
  );
}

/** A plain progress bar (display ratio of two engine numbers, capped at 100%). */
export function ProgressBar({ value, max, label }: { value: number; max: number | null; label: string }) {
  const frac = max === null || max <= 0 ? 0 : Math.min(1, value / max);
  return (
    <div
      className="progress"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max ?? 0}
      aria-valuenow={value}
    >
      <div className="progress-fill" style={{ width: `${(frac * 100).toFixed(1)}%` }} />
    </div>
  );
}

/** Calibration bins: predicted vs observed rate per bin, dot size by n. */
export function CalibrationChart({
  bins,
}: {
  bins: readonly { lo_rate: number; hi_rate: number; n: number; predicted_rate: number | null; observed_rate: number | null }[];
}) {
  const pts = bins.filter((b) => b.predicted_rate !== null && b.observed_rate !== null && b.n > 0);
  const lo = Math.min(...bins.map((b) => b.lo_rate), 1);
  const hi = Math.max(...bins.map((b) => b.hi_rate), 0);
  const S = 160;
  const p = (v: number) => 10 + ((v - lo) / (hi - lo || 1)) * (S - 20);
  const maxN = Math.max(1, ...pts.map((b) => b.n));
  return (
    <svg className="chart-calibration" viewBox={`0 0 ${S} ${S}`} role="img" aria-label="Calibration: predicted vs observed">
      <rect className="chart-frame" x={10} y={10} width={S - 20} height={S - 20} />
      <line className="chart-ref" x1={p(lo)} y1={S - p(lo)} x2={p(hi)} y2={S - p(hi)} />
      {pts.map((b) => (
        <circle
          key={b.lo_rate}
          className="chart-point chart-mid"
          cx={p(b.predicted_rate ?? 0)}
          cy={S - p(b.observed_rate ?? 0)}
          r={2 + 4 * Math.sqrt(b.n / maxN)}
        />
      ))}
    </svg>
  );
}

export const SERIES_COLORS = chartSeriesColors;

export interface LineSeries {
  label: string;
  color: string;
  values: (number | null)[];
  /** Drawn as a filled area to zero (drawdown). */
  fill?: boolean;
  /** Dash pattern in px (e.g. [4, 4]) for a reference line. */
  dash?: number[];
}

function canDraw(): boolean {
  // jsdom (unit tests) has no matchMedia/canvas; the chart's text summary still renders.
  return typeof window !== "undefined" && typeof window.matchMedia === "function";
}

/**
 * Line chart over a shared x axis (Unix seconds when `time`, else trade number). uPlot is
 * loaded on first use so tests and screens without charts never pay for it.
 */
export function LineChart({
  x,
  series,
  time,
  height = 180,
  label,
  yFormat,
}: {
  x: number[];
  series: LineSeries[];
  time: boolean;
  height?: number;
  label: string;
  yFormat: (v: number) => string;
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current;
    if (el === null || !canDraw() || x.length === 0) return;
    let plot: uPlot | null = null;
    let cancelled = false;
    let ro: ResizeObserver | null = null;
    void import("uplot").then(({ default: UPlot }) => {
      if (cancelled) return;
      const axis = {
        stroke: colors.dim,
        grid: { stroke: colors.border, width: 1 },
        ticks: { stroke: colors.border, width: 1 },
        font: "11px JetBrains Mono, monospace",
      };
      const opts: uPlot.Options = {
        width: Math.max(200, el.clientWidth),
        height,
        legend: { show: false },
        // Contract timestamps are UTC; label the axis in UTC too, like every other screen.
        tzDate: (t: number) => UPlot.tzDate(new Date(t * 1000), "Etc/UTC"),
        cursor: { drag: { x: true, y: false } },
        scales: { x: { time } },
        axes: [
          axis,
          { ...axis, size: 64, values: (_u: uPlot, vals: number[]) => vals.map((v) => yFormat(v)) },
        ],
        series: [
          {},
          ...series.map((s) => ({
            label: s.label,
            stroke: s.color,
            width: 1.5,
            spanGaps: false,
            points: { show: false },
            ...(s.fill === true ? { fill: derivedColors["down-soft"] } : {}),
            ...(s.dash !== undefined ? { dash: s.dash, width: 1 } : {}),
          })),
        ],
      };
      plot = new UPlot(opts, [x, ...series.map((s) => s.values)] as uPlot.AlignedData, el);
      ro = new ResizeObserver(() => plot?.setSize({ width: Math.max(200, el.clientWidth), height }));
      ro.observe(el);
    });
    return () => {
      cancelled = true;
      ro?.disconnect();
      plot?.destroy();
    };
  }, [x, series, time, height, yFormat]);
  return <div ref={box} className="chart-line" role="img" aria-label={label} style={{ minHeight: height }} />;
}
