/**
 * Top-of-book over time (YES bid and YES ask), sampled each time the book is polled while
 * this page is open. SVG keeps it inside the strict CSP (no canvas/data URLs needed) and
 * testable in jsdom; a few hundred points is well within what SVG draws smoothly.
 */
export interface TopSample {
  t: number;
  bid: number | null;
  ask: number | null;
}

export const MAX_SAMPLES = 720;

const W = 600;
const H = 160;
const PAD = { l: 44, r: 8, t: 8, b: 18 };

function path(samples: readonly TopSample[], pick: (s: TopSample) => number | null, x: (t: number) => number, y: (p: number) => number): string {
  let d = "";
  let pen = false;
  for (const s of samples) {
    const p = pick(s);
    if (p === null) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${x(s.t).toFixed(1)},${y(p).toFixed(1)}`;
    pen = true;
  }
  return d;
}

export function TopOfBookChart({ samples }: { samples: readonly TopSample[] }) {
  const prices = samples.flatMap((s) => [s.bid, s.ask]).filter((p): p is number => p !== null);
  if (samples.length < 2 || prices.length === 0) {
    return (
      <p className="dim chart-empty" role="status">
        Collecting top of book ({samples.length} sample{samples.length === 1 ? "" : "s"}, one per refresh)...
      </p>
    );
  }
  const t0 = samples[0]?.t ?? 0;
  const t1 = samples.at(-1)?.t ?? t0 + 1;
  let lo = Math.min(...prices);
  let hi = Math.max(...prices);
  if (hi - lo < 0.02) {
    const mid = (hi + lo) / 2;
    lo = mid - 0.01;
    hi = mid + 0.01;
  }
  lo = Math.max(0, lo - 0.005);
  hi = Math.min(1, hi + 0.005);
  const x = (t: number) => PAD.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - PAD.l - PAD.r);
  const y = (p: number) => PAD.t + (1 - (p - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
  const ticks = [lo, (lo + hi) / 2, hi];
  const span = Math.round((t1 - t0) / 1000);
  return (
    <figure className="tob-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Top of book chart" preserveAspectRatio="none">
        {ticks.map((p) => (
          <g key={p}>
            <line className="tob-grid" x1={PAD.l} x2={W - PAD.r} y1={y(p)} y2={y(p)} />
            <text className="tob-tick" x={PAD.l - 4} y={y(p) + 4} textAnchor="end">
              {(p * 100).toFixed(1)}
            </text>
          </g>
        ))}
        <path className="tob-ask" d={path(samples, (s) => s.ask, x, y)} />
        <path className="tob-bid" d={path(samples, (s) => s.bid, x, y)} />
        <text className="tob-tick" x={W - PAD.r} y={H - 4} textAnchor="end">
          last {span}s
        </text>
      </svg>
      <figcaption className="tob-legend">
        <span className="tob-key tob-key-ask">YES ask</span> <span className="tob-key tob-key-bid">YES bid</span>{" "}
        <span className="dim">(cents, {samples.length} samples)</span>
      </figcaption>
    </figure>
  );
}
