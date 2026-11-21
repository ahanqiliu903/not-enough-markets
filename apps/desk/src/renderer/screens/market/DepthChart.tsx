import { useEffect, useMemo, useRef } from "react";
import type { DbLevel } from "@shared/db";
import { colors, derivedColors } from "../../theme";
import { formatPrice, formatQty } from "../explore/format";

/**
 * Order book depth for one recorded snapshot, drawn on canvas (spec Section 2). Kalshi books
 * hold bids only: YES bids are buyers of YES; NO bids at p are sellers of YES at 1 - p. The
 * chart is in YES price: cumulative YES bids to the left, cumulative implied YES asks
 * (from NO bids) to the right. Sizes are the recorded ones; nothing is simulated.
 */

export interface DepthStep {
  price: number;
  cum: number;
}

/** Cumulative size from the best price outward, for both sides, in YES price. */
export function depthCurves(yes: readonly DbLevel[], no: readonly DbLevel[]): { bids: DepthStep[]; asks: DepthStep[] } {
  const bids = [...yes].sort((a, b) => b[0] - a[0]);
  const asks = no.map(([p, q]): DbLevel => [Math.round((1 - p) * 1e6) / 1e6, q]).sort((a, b) => a[0] - b[0]);
  const cum = (levels: DbLevel[]): DepthStep[] => {
    let c = 0;
    return levels.map(([price, q]) => {
      c += q;
      return { price, cum: c };
    });
  };
  return { bids: cum(bids), asks: cum(asks) };
}

function canDraw(): boolean {
  // jsdom has no canvas; the levels table below still renders.
  return typeof window !== "undefined" && typeof window.matchMedia === "function";
}

const H = 180;
const PAD = { l: 48, r: 10, t: 10, b: 20 };

function draw(canvas: HTMLCanvasElement, bids: DepthStep[], asks: DepthStep[]): void {
  const ctx = canvas.getContext("2d");
  if (ctx === null) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(H * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, H);
  const prices = [...bids, ...asks].map((s) => s.price);
  if (prices.length === 0) return;
  let lo = Math.min(...prices);
  let hi = Math.max(...prices);
  const margin = Math.max(0.01, (hi - lo) * 0.1);
  lo = Math.max(0, lo - margin);
  hi = Math.min(1, hi + margin);
  const maxCum = Math.max(1, ...bids.map((s) => s.cum), ...asks.map((s) => s.cum));
  const x = (p: number) => PAD.l + ((p - lo) / (hi - lo || 1)) * (w - PAD.l - PAD.r);
  const y = (c: number) => PAD.t + (1 - c / maxCum) * (H - PAD.t - PAD.b);

  ctx.font = "11px JetBrains Mono, monospace";
  ctx.fillStyle = colors.dim;
  ctx.strokeStyle = colors.border;
  ctx.lineWidth = 1;
  for (const f of [0, 0.5, 1]) {
    const yy = Math.round(y(maxCum * f)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(PAD.l, yy);
    ctx.lineTo(w - PAD.r, yy);
    ctx.stroke();
    ctx.textAlign = "right";
    ctx.fillText(formatQty(maxCum * f), PAD.l - 4, yy + 4);
  }
  ctx.textAlign = "center";
  for (const p of [lo, (lo + hi) / 2, hi]) ctx.fillText(formatPrice(Math.round(p * 1000) / 1000), x(p), H - 6);

  const side = (steps: DepthStep[], dir: -1 | 1, stroke: string, fill: string) => {
    if (steps.length === 0) return;
    ctx.beginPath();
    const base = y(0);
    const first = steps[0];
    if (first === undefined) return;
    ctx.moveTo(x(first.price), base);
    let prevY = base;
    for (const s of steps) {
      ctx.lineTo(x(s.price), prevY);
      ctx.lineTo(x(s.price), y(s.cum));
      prevY = y(s.cum);
    }
    const edge = dir < 0 ? lo : hi;
    ctx.lineTo(x(edge), prevY);
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.lineTo(x(edge), base);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
  };
  side(bids, -1, colors.up, derivedColors["up-soft"]);
  side(asks, 1, colors.down, derivedColors["down-soft"]);
}

export function DepthChart({ yes, no, ts }: { yes: readonly DbLevel[]; no: readonly DbLevel[]; ts: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const { bids, asks } = useMemo(() => depthCurves(yes, no), [yes, no]);
  useEffect(() => {
    const el = ref.current;
    if (el === null || !canDraw()) return;
    draw(el, bids, asks);
    const ro = new ResizeObserver(() => draw(el, bids, asks));
    ro.observe(el);
    return () => ro.disconnect();
  }, [bids, asks]);
  const rows = Math.max(Math.min(10, yes.length), Math.min(10, no.length));
  const empty = yes.length === 0 && no.length === 0;
  return (
    <figure className="depth">
      {empty ? (
        <p className="dim">No depth recorded in this snapshot.</p>
      ) : (
        <canvas ref={ref} className="depth-canvas" role="img" aria-label={`Order book depth at ${ts}`} height={H} />
      )}
      <figcaption className="tob-legend">
        <span className="up">YES bids (cumulative)</span> <span className="down">YES asks from NO bids</span>{" "}
        <span className="dim">snapshot {ts}</span>
      </figcaption>
      <table className="grid depth-levels" aria-label="Order book levels">
        <thead>
          <tr>
            <th className="num">YES bid</th>
            <th className="num">Size</th>
            <th className="num">NO bid</th>
            <th className="num">Size</th>
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, i) => {
            const y = yes[i];
            const n = no[i];
            return (
              <tr key={i}>
                <td className="num up">{y !== undefined ? formatPrice(y[0]) : ""}</td>
                <td className="num">{y !== undefined ? formatQty(y[1]) : ""}</td>
                <td className="num down">{n !== undefined ? formatPrice(n[0]) : ""}</td>
                <td className="num">{n !== undefined ? formatQty(n[1]) : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </figure>
  );
}
