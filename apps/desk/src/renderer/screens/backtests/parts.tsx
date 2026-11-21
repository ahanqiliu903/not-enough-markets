import type { BacktestRun, VariantMetrics } from "@shared/data";
import { cents, int, num, pct, range, signClass, signedUsd, ts, usd } from "../../data/format";
import { buildHeatmap, showValue, windowSpans, type HeatCell } from "./model";

/** Run status as the engine wrote it. Amber/neutral, never tier colors. */
export function RunStatusTag({ status }: { status: BacktestRun["status"] }) {
  return (
    <span className={`tag run-status run-status-${status}`} data-run-status={status}>
      {status.toUpperCase()}
    </span>
  );
}

type Col = { label: string; metrics: VariantMetrics | null; missing: string };

const ROWS: { label: string; show: (m: VariantMetrics) => string; cls?: (m: VariantMetrics) => string }[] = [
  { label: "Settled trades", show: (m) => int(m.trades) },
  { label: "Wins", show: (m) => int(m.wins) },
  { label: "Win rate", show: (m) => pct(m.win_rate) },
  { label: "95% interval", show: (m) => range(pct(m.win_rate_lo), pct(m.win_rate_hi)) },
  { label: "Break-even", show: (m) => pct(m.break_even_rate) },
  { label: "Edge / contract", show: (m) => cents(m.edge_cents), cls: (m) => signClass(m.edge_cents) },
  { label: "Edge interval", show: (m) => range(cents(m.edge_lo_cents), cents(m.edge_hi_cents)) },
  { label: "z / p", show: (m) => `${num(m.z)} / ${num(m.p)}` },
  { label: "P&L", show: (m) => signedUsd(m.pnl_usd), cls: (m) => signClass(m.pnl_usd) },
  { label: "P&L per trade", show: (m) => signedUsd(m.pnl_per_trade_usd, 4) },
  { label: "ROI", show: (m) => pct(m.roi) },
  { label: "Max drawdown", show: (m) => usd(m.max_drawdown_usd) },
  {
    label: "Trades to detect",
    show: (m) => (m.trades_to_detect === null ? "-" : `${int(m.trades_to_detect)} (${cents(m.trades_to_detect_edge_cents)})`),
  },
  { label: "Verdict (SPRT)", show: (m) => m.verdict.replace("_", " ") },
  { label: "Brier", show: (m) => num(m.brier) },
];

/** Engine metrics side by side; a missing column says why instead of showing dashes. */
export function MetricsTable({ label, cols }: { label: string; cols: Col[] }) {
  return (
    <div className="grid-wrap">
      <table className="grid metrics-table" aria-label={label}>
        <thead>
          <tr>
            <th>Metric</th>
            {cols.map((c) => (
              <th key={c.label} className="num">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ROWS.map((r, i) => (
            <tr key={r.label}>
              <td className="dim">{r.label}</td>
              {cols.map((c) =>
                c.metrics === null ? (
                  i === 0 ? (
                    <td key={c.label} className="dim metrics-missing" rowSpan={ROWS.length}>
                      {c.missing}
                    </td>
                  ) : null
                ) : (
                  <td key={c.label} className={`num ${r.cls?.(c.metrics) ?? ""}`}>
                    {r.show(c.metrics)}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Train and held-out test windows on one time line. */
export function WindowsBar({ run }: { run: BacktestRun }) {
  const spans = windowSpans(run);
  const W = 400;
  return (
    <div>
      {spans !== null && (
        <svg className="chart-windows" viewBox={`0 0 ${W} 26`} role="img" aria-label="Train and held-out test windows" preserveAspectRatio="none">
          <rect className="window-train" x={spans.train[0] * W} y={4} width={Math.max(1, (spans.train[1] - spans.train[0]) * W)} height={12} />
          <rect className="window-test" x={spans.test[0] * W} y={4} width={Math.max(1, (spans.test[1] - spans.test[0]) * W)} height={12} />
        </svg>
      )}
      <table className="grid" aria-label="Windows">
        <tbody>
          <tr>
            <td><span className="legend-swatch window-train-swatch" />train (tuned on)</td>
            <td>{ts(run.train_window.start)}</td>
            <td className="dim">to</td>
            <td>{ts(run.train_window.end)}</td>
          </tr>
          <tr>
            <td><span className="legend-swatch window-test-swatch" />test (held out)</td>
            <td>{ts(run.test_window.start)}</td>
            <td className="dim">to</td>
            <td>{ts(run.test_window.end)}</td>
          </tr>
        </tbody>
      </table>
      <p className="dim">Windows are half-open [start, end). Only the chosen variant was ever scored on the test window.</p>
    </div>
  );
}

function cellTitle(c: HeatCell, run: BacktestRun): string {
  if (c.variant === null) return "no variant at this point";
  const o = Object.entries(c.variant.overrides).map(([k, v]) => `${k}=${showValue(v)}`).join(", ");
  const parts = [`${c.variant.name}: ${o}`, `train edge ${cents(c.edge)}`, `n ${int(c.n)}`];
  if (c.muted) parts.push(`under min_trades (${int(run.min_trades)}): not considered`);
  if (c.chosen) parts.push("chosen");
  return parts.join("; ");
}

/** DESK-016: train edge per grid cell with its sample size; chosen marked, thin cells muted. */
export function SweepHeatmap({ run }: { run: BacktestRun }) {
  const hm = buildHeatmap(run);
  if (hm === null) {
    return <p className="dim">The grid has {run.grid.length} axes; the heatmap draws 1 or 2. Every variant is in the table below.</p>;
  }
  return (
    <div className="grid-wrap">
      <table className="heatmap" aria-label="Sweep heatmap">
        <thead>
          <tr>
            <th className="dim heat-corner">
              {hm.rowAxis !== null ? `${hm.rowAxis.path} \\ ` : ""}
              {hm.colAxis.path}
            </th>
            {hm.colAxis.values.map((v, i) => (
              <th key={i} className="num">
                {showValue(v)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {hm.cells.map((row, r) => (
            <tr key={r}>
              <th className="num">{hm.rowAxis !== null ? showValue(hm.rowAxis.values[r]) : ""}</th>
              {row.map((c, i) => (
                <td
                  key={i}
                  className={`heat-cell heat-${c.bucket}${c.muted ? " heat-muted" : ""}${c.chosen ? " heat-chosen" : ""}`}
                  title={cellTitle(c, run)}
                  aria-label={cellTitle(c, run)}
                  data-variant={c.variant?.name}
                >
                  {c.variant === null ? (
                    <span className="dim">-</span>
                  ) : (
                    <>
                      <span className="heat-edge num">{cents(c.edge, 1)}</span>
                      <span className="heat-n">n {int(c.n)}</span>
                      {c.chosen && <span className="heat-mark">CHOSEN</span>}
                    </>
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="dim">
        Cell: train edge per contract (engine) and train settled trades n. Color is the sign and relative size of the
        edge, not a verdict. Muted cells have fewer than {int(run.min_trades)} train trades (min_trades) and were not
        considered. Test results exist only for the chosen cell.
      </p>
    </div>
  );
}

/** Every variant with its overrides and train numbers (the heatmap's data as a table). */
export function VariantsTable({ run }: { run: BacktestRun }) {
  return (
    <div className="grid-wrap">
      <table className="grid" aria-label="Variants">
        <thead>
          <tr>
            <th>Variant</th>
            {run.grid.map((g) => (
              <th key={g.path}>{g.path}</th>
            ))}
            <th className="num">Train n</th>
            <th className="num">Train win rate</th>
            <th className="num">Train edge</th>
            <th className="num">Edge interval</th>
            <th>Test</th>
          </tr>
        </thead>
        <tbody>
          {run.variants.map((v) => (
            <tr key={v.name} className={`${v.name === run.chosen ? "selected" : ""}${v.train.trades < run.min_trades ? " retired" : ""}`}>
              <td>{v.name}{v.name === run.chosen ? " (chosen)" : ""}</td>
              {run.grid.map((g) => (
                <td key={g.path} className="num">{showValue(v.overrides[g.path])}</td>
              ))}
              <td className="num">{int(v.train.trades)}</td>
              <td className="num">{pct(v.train.win_rate)}</td>
              <td className={`num ${signClass(v.train.edge_cents)}`}>{cents(v.train.edge_cents)}</td>
              <td className="num">{range(cents(v.train.edge_lo_cents), cents(v.train.edge_hi_cents))}</td>
              <td className="dim">{v.test === null ? "not run (by design)" : `n ${int(v.test.trades)}, edge ${cents(v.test.edge_cents)}`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
