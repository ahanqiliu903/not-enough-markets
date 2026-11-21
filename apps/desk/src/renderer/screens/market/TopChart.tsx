import { useEffect, useMemo, useRef } from "react";
import type uPlot from "uplot";
import type { DbFeedPoint, DbTopPoint } from "@shared/db";
import { chartSeriesColors, colors } from "../../theme";
import "uplot/dist/uPlot.min.css";

/**
 * Recorded top of book (YES bid / ask, cents) over a market's life in uPlot, with an
 * optional feed on a right-hand axis. Feed values are drawn at their `known_at` (when each
 * value became knowable, the time the engine could have used it), with a tick per value
 * along the bottom. Clicking the chart picks the nearest snapshot for the depth view.
 */

export interface Aligned {
  x: number[];
  bid: (number | null)[];
  ask: (number | null)[];
  feed: (number | null)[];
}

/** One shared, sorted x axis for snapshots and feed values (uPlot needs aligned data). */
export function alignSeries(points: readonly DbTopPoint[], feed: readonly DbFeedPoint[]): Aligned {
  const top = new Map<number, DbTopPoint>();
  for (const p of points) top.set(p.t, p);
  const fv = new Map<number, number>();
  for (const f of feed) if (Number.isFinite(f.value)) fv.set(f.knownAt, f.value);
  const x = [...new Set([...top.keys(), ...fv.keys()])].filter(Number.isFinite).sort((a, b) => a - b);
  const c = (v: number | null | undefined) => (v === null || v === undefined ? null : Math.round(v * 10000) / 100);
  return {
    x,
    bid: x.map((t) => c(top.get(t)?.yesBid)),
    ask: x.map((t) => c(top.get(t)?.yesAsk)),
    feed: x.map((t) => fv.get(t) ?? null),
  };
}

/** The snapshot closest in time to `t`. */
export function nearestPoint(points: readonly DbTopPoint[], t: number): DbTopPoint | null {
  let best: DbTopPoint | null = null;
  for (const p of points) if (best === null || Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
  return best;
}

function canDraw(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function";
}

const HEIGHT = 220;
const FEED_COLOR = chartSeriesColors[1];

export function TopChart({
  points,
  feed,
  feedLabel,
  selectedT,
  onPick,
}: {
  points: readonly DbTopPoint[];
  feed: readonly DbFeedPoint[];
  feedLabel: string | null;
  selectedT: number | null;
  onPick: (p: DbTopPoint) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const pick = useRef(onPick);
  const sel = useRef(selectedT);
  useEffect(() => {
    pick.current = onPick;
  }, [onPick]);
  const data = useMemo(() => alignSeries(points, feed), [points, feed]);
  const knownAts = useMemo(() => feed.map((f) => f.knownAt), [feed]);

  useEffect(() => {
    sel.current = selectedT;
    plotRef.current?.redraw(false, false);
  }, [selectedT]);

  useEffect(() => {
    const el = box.current;
    if (el === null || !canDraw() || data.x.length === 0) return;
    let plot: uPlot | null = null;
    let cancelled = false;
    let ro: ResizeObserver | null = null;
    const onClick = () => {
      const idx = plot?.cursor.idx;
      const t = idx !== null && idx !== undefined ? data.x[idx] : undefined;
      if (t === undefined) return;
      const p = nearestPoint(points, t);
      if (p !== null) pick.current(p);
    };
    void import("uplot").then(({ default: UPlot }) => {
      if (cancelled) return;
      const axis = {
        stroke: colors.dim,
        grid: { stroke: colors.border, width: 1 },
        ticks: { stroke: colors.border, width: 1 },
        font: "11px JetBrains Mono, monospace",
      };
      const hasFeed = feedLabel !== null && knownAts.length > 0;
      const opts: uPlot.Options = {
        width: Math.max(240, el.clientWidth),
        height: HEIGHT,
        legend: { show: false },
        tzDate: (t: number) => UPlot.tzDate(new Date(t * 1000), "Etc/UTC"),
        cursor: { drag: { x: false, y: false } },
        scales: { x: { time: true }, c: { auto: true }, f: { auto: true } },
        axes: [
          axis,
          { ...axis, scale: "c", size: 48, values: (_u: uPlot, vals: number[]) => vals.map((v) => `${v}c`) },
          ...(hasFeed
            ? [
                {
                  ...axis,
                  scale: "f",
                  // uPlot's Axis.Side is an ambient const enum, unusable under isolatedModules; 1 = Right.
                  // eslint-disable-next-line @typescript-eslint/no-unsafe-enum-assignment
                  side: 1 as uPlot.Axis.Side,
                  size: 72,
                  grid: { show: false },
                },
              ]
            : []),
        ],
        series: [
          {},
          { label: "YES bid", scale: "c", stroke: colors.up, width: 1.5, spanGaps: true, points: { show: false } },
          { label: "YES ask", scale: "c", stroke: colors.down, width: 1.5, spanGaps: true, points: { show: false } },
          {
            label: feedLabel ?? "feed",
            scale: "f",
            show: hasFeed,
            stroke: FEED_COLOR,
            width: 1,
            spanGaps: true,
            points: { show: true, size: 4, stroke: FEED_COLOR, fill: FEED_COLOR },
          },
        ],
        hooks: {
          draw: [
            (u: uPlot) => {
              const ctx = u.ctx;
              const { left, top, width, height } = u.bbox;
              ctx.save();
              if (hasFeed) {
                // known_at markers: one tick per feed value along the bottom of the plot.
                ctx.strokeStyle = FEED_COLOR;
                ctx.lineWidth = 1;
                ctx.beginPath();
                for (const t of knownAts) {
                  const px = Math.round(u.valToPos(t, "x", true)) + 0.5;
                  if (px < left || px > left + width) continue;
                  ctx.moveTo(px, top + height);
                  ctx.lineTo(px, top + height - 6 * devicePixelRatio);
                }
                ctx.stroke();
              }
              const s = sel.current;
              if (s !== null) {
                const px = Math.round(u.valToPos(s, "x", true)) + 0.5;
                ctx.strokeStyle = colors.amber;
                ctx.setLineDash([3 * devicePixelRatio, 3 * devicePixelRatio]);
                ctx.beginPath();
                ctx.moveTo(px, top);
                ctx.lineTo(px, top + height);
                ctx.stroke();
              }
              ctx.restore();
            },
          ],
        },
      };
      plot = new UPlot(opts, [data.x, data.bid, data.ask, data.feed] as uPlot.AlignedData, el);
      plotRef.current = plot;
      plot.over.addEventListener("click", onClick);
      ro = new ResizeObserver(() => plot?.setSize({ width: Math.max(240, el.clientWidth), height: HEIGHT }));
      ro.observe(el);
    });
    return () => {
      cancelled = true;
      ro?.disconnect();
      plot?.over.removeEventListener("click", onClick);
      plot?.destroy();
      plotRef.current = null;
    };
  }, [data, knownAts, feedLabel, points]);

  return (
    <figure className="market-top">
      <div ref={box} className="chart-line" role="img" aria-label="Recorded top of book" style={{ minHeight: HEIGHT }} />
      <figcaption className="tob-legend">
        <span className="up">YES bid</span> <span className="down">YES ask</span>{" "}
        {feedLabel !== null && (
          <span className="market-feed-key">
            {feedLabel} at known_at ({feed.length} values, ticks mark each)
          </span>
        )}{" "}
        <span className="dim">({points.length} snapshots, UTC; click to show that snapshot&apos;s depth)</span>
      </figcaption>
    </figure>
  );
}
