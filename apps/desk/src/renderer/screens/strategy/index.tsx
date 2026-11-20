import { useMemo } from "react";
import type { PnlCurve, Snapshot, StrategyExtras, StrategyRow } from "@shared/data";
import { EmptyState } from "../../components/states";
import { CalibrationChart, IntervalChart, LineChart, ProgressBar } from "../../data/charts";
import { useCompare } from "../../data/compare";
import { cents, int, num, pct, range, signClass, signedUsd, usd } from "../../data/format";
import { allStrategies, findStrategy, useCurves } from "../../data/hooks";
import { drawdownSeries } from "../../data/series";
import { DataErrors, SourceGate } from "../../data/states";
import { StrategyBadge } from "../../data/StrategyBadge";
import { useKeys } from "../../lib/keys";
import { colors } from "../../theme";
import { useNav } from "../../state/nav";

/** Screen 2: one strategy's verdict, intervals, SPRT progress, P&L and calibration. */
export default function StrategyScreen() {
  return (
    <SourceGate
      emptyTitle="No strategy selected"
      emptyReason="Strategy results come from the engine's export."
    >
      {(s) => <Strategy snapshot={s} />}
    </SourceGate>
  );
}

function Strategy({ snapshot }: { snapshot: Snapshot }) {
  const params = useNav((s) => s.params);
  const navigate = useNav((s) => s.navigate);
  const ref = findStrategy(snapshot, params["portfolio"], params["strategy"]);
  const all = allStrategies(snapshot);

  if (ref === null) {
    return (
      <EmptyState
        title="No strategy selected"
        reason={
          all.length === 0
            ? "This source has no strategies yet."
            : params["strategy"] !== undefined
              ? `Strategy ${params["portfolio"] ?? ""}/${params["strategy"]} is not in this source. Pick one:`
              : "Pick a strategy (or open one from Portfolios, Cmd/Ctrl+1):"
        }
      >
        <ul className="plain-list">
          {all.map((r) => (
            <li key={r.id}>
              <button type="button" onClick={() => navigate("strategy", { portfolio: r.portfolio.name, strategy: r.row.strategy })}>
                {r.row.strategy}
              </button>{" "}
              <span className="dim">{r.portfolio.name}</span>
            </li>
          ))}
        </ul>
      </EmptyState>
    );
  }
  return <Detail row={ref.row} portfolio={ref.portfolio.name} extras={snapshot.extras[ref.id]} hasSummaryJson={snapshot.hasSummaryJson} />;
}

/** Domain for the win-rate chart: the interval and break-even with some margin, in [0, 1]. */
function rateDomain(r: StrategyRow): [number, number] {
  const vals = [r.win_rate_lo, r.win_rate_hi, r.break_even_rate, r.win_rate].filter((v): v is number => v !== null);
  if (vals.length === 0) return [0, 1];
  const lo = Math.max(0, Math.min(...vals) - 0.05);
  const hi = Math.min(1, Math.max(...vals) + 0.05);
  return [lo, hi];
}

function edgeDomain(r: StrategyRow): [number, number] {
  const vals = [r.edge_lo_cents, r.edge_hi_cents, r.edge_cents, 0].filter((v): v is number => v !== null);
  const pad = Math.max(1, (Math.max(...vals) - Math.min(...vals)) * 0.15);
  return [Math.min(...vals) - pad, Math.max(...vals) + pad];
}

const fmtPct1 = (v: number) => pct(v, 1);
const fmtCents1 = (v: number) => cents(v, 1);
const fmtUsd0 = (v: number) => usd(v, v !== 0 && Math.abs(v) < 10 ? 2 : 0);

function Detail({
  row: r,
  portfolio,
  extras,
  hasSummaryJson,
}: {
  row: StrategyRow;
  portfolio: string;
  extras: StrategyExtras | undefined;
  hasSummaryJson: boolean;
}) {
  const navigate = useNav((s) => s.navigate);
  const compare = useCompare();
  const id = `${portfolio}/${r.strategy}`;
  const picked = compare.ids.includes(id);
  useKeys({
    c: () => compare.toggle(id),
    t: () => navigate("trades", { portfolio, strategy: r.strategy }),
  });

  return (
    <div className="screen-stack" aria-label={`Strategy ${r.strategy}`}>
      <div className="card-head">
        <StrategyBadge row={r} />
        <h2 className="card-title pixel-font">{r.strategy}</h2>
        <span className="dim">
          {portfolio} / {r.series}
        </span>
        <span className="tag">{r.status.toUpperCase()}</span>
        <span className="spacer" />
        <button type="button" onClick={() => navigate("trades", { portfolio, strategy: r.strategy })}>
          Trades (t)
        </button>
        <button type="button" onClick={() => compare.toggle(id)} aria-pressed={picked}>
          {picked ? "In compare (c)" : "Add to compare (c)"}
        </button>
      </div>

      <div className="detail-grid">
        <section className="panel-box" aria-label="Win rate vs break-even">
          <h3>Win rate vs break-even</h3>
          <IntervalChart
            label="Win rate interval vs break-even"
            value={r.win_rate}
            lo={r.win_rate_lo}
            hi={r.win_rate_hi}
            reference={r.break_even_rate}
            referenceLabel="break-even"
            domain={rateDomain(r)}
            format={fmtPct1}
          />
          <p>
            win rate <b className="num">{pct(r.win_rate)}</b> <span className="dim">95% interval</span>{" "}
            <span className="num">{range(pct(r.win_rate_lo), pct(r.win_rate_hi))}</span>{" "}
            <span className="dim">break-even</span> <span className="num">{pct(r.break_even_rate)}</span>
          </p>
          <p className="dim">
            {int(r.wins)} won of {int(r.trades)} settled; {int(r.open)} open. z {num(r.z)}, p {num(r.p)}.
          </p>
        </section>

        <section className="panel-box" aria-label="Edge per contract">
          <h3>Edge per contract</h3>
          <IntervalChart
            label="Edge interval vs zero"
            value={r.edge_cents}
            lo={r.edge_lo_cents}
            hi={r.edge_hi_cents}
            reference={r.edge_cents === null ? null : 0}
            referenceLabel="zero"
            domain={edgeDomain(r)}
            format={fmtCents1}
          />
          <p>
            edge <b className={`num ${signClass(r.edge_cents)}`}>{cents(r.edge_cents)}</b>{" "}
            <span className="dim">95% interval</span>{" "}
            <span className="num">{range(cents(r.edge_lo_cents), cents(r.edge_hi_cents))}</span>
          </p>
          <p className="dim">Mean payout minus cost per contract, after fees (engine).</p>
        </section>

        <section className="panel-box" aria-label="Sequential test progress">
          <h3>Sequential test (SPRT)</h3>
          <ProgressBar value={r.trades} max={r.trades_to_detect} label="Settled trades vs trades to detect" />
          <p>
            <span className="num">{int(r.trades)}</span> settled of{" "}
            <span className="num">{r.trades_to_detect === null ? "-" : int(r.trades_to_detect)}</span> needed to detect a{" "}
            <span className="num">{cents(r.trades_to_detect_edge_cents)}</span> edge (5% / 80% power).
          </p>
          <p>
            verdict: <b>{r.verdict.replace("_", " ")}</b>
            {r.verdict === "undecided" && <span className="dim"> - keep trading; this is the expected answer for weeks.</span>}
          </p>
        </section>

        <section className="panel-box" aria-label="Other statistics">
          <h3>Statistics</h3>
          <table className="grid">
            <tbody>
              <tr><td className="dim">P&L</td><td className={`num ${signClass(r.pnl_usd)}`}>{signedUsd(r.pnl_usd)}</td></tr>
              <tr><td className="dim">P&L per trade</td><td className="num">{signedUsd(r.pnl_per_trade_usd, 4)}</td></tr>
              <tr><td className="dim">ROI</td><td className="num">{pct(r.roi)}</td></tr>
              <tr><td className="dim">Max drawdown</td><td className="num">{usd(r.max_drawdown_usd)}</td></tr>
              <tr><td className="dim">Brier</td><td className="num">{num(r.brier)}</td></tr>
            </tbody>
          </table>
        </section>
      </div>

      <PnlPanel portfolio={portfolio} strategy={r.strategy} maxDrawdown={r.max_drawdown_usd} />

      <div className="detail-grid">
        <section className="panel-box" aria-label="Calibration">
          <h3>Calibration</h3>
          {extras === undefined ? (
            <p className="dim">{hasSummaryJson ? "summary.json has no entry for this strategy." : "No summary.json in this source. Run `uv run nem summary --json > summary.json` next to out/."}</p>
          ) : extras.calibration.length === 0 ? (
            <p className="dim">No calibration bins yet.</p>
          ) : (
            <div className="row-wrap">
              <CalibrationChart bins={extras.calibration} />
              <table className="grid" aria-label="Calibration bins">
                <thead>
                  <tr><th>Bin</th><th className="num">n</th><th className="num">Predicted</th><th className="num">Observed</th></tr>
                </thead>
                <tbody>
                  {extras.calibration.map((b) => (
                    <tr key={b.lo_rate}>
                      <td>{range(pct(b.lo_rate, 0), pct(b.hi_rate, 0))}</td>
                      <td className="num">{int(b.n)}</td>
                      <td className="num">{pct(b.predicted_rate, 1)}</td>
                      <td className="num">{pct(b.observed_rate, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <section className="panel-box" aria-label="Signal reasons">
          <h3>Signal reasons</h3>
          {extras === undefined ? (
            <p className="dim">Needs summary.json (see Calibration).</p>
          ) : (
            <table className="grid" aria-label="Signal reasons">
              <thead>
                <tr><th>Reason</th><th className="num">Signals</th></tr>
              </thead>
              <tbody>
                {Object.entries(extras.signal_reasons)
                  .sort((a, b) => b[1] - a[1])
                  .map(([reason, n]) => (
                    <tr key={reason}><td>{reason}</td><td className="num">{int(n)}</td></tr>
                  ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  );
}

function PnlPanel({ portfolio, strategy, maxDrawdown }: { portfolio: string; strategy: string; maxDrawdown: number }) {
  const keys = useMemo(() => [{ portfolio, strategy }], [portfolio, strategy]);
  const q = useCurves(keys);
  const curve: PnlCurve | undefined = q.data?.ok === true ? q.data.curves[0] : undefined;
  const series = useMemo(() => {
    if (curve === undefined) return null;
    return {
      pnl: [{ label: "cumulative P&L", color: colors.amber, values: curve.pnl }],
      dd: [{ label: "drawdown", color: colors.down, values: drawdownSeries(curve.pnl), fill: true }],
    };
  }, [curve]);
  return (
    <section className="panel-box" aria-label="P&L curve">
      <h3>P&L curve and drawdown</h3>
      {q.isPending ? (
        <p className="dim">Loading trades...</p>
      ) : q.data?.ok === false ? (
        <DataErrors errors={q.data.errors} />
      ) : curve === undefined || series === null || curve.t.length === 0 ? (
        <p className="dim">No settled trades yet.</p>
      ) : (
        <>
          <LineChart x={curve.t} series={series.pnl} time label="Cumulative realized P&L" yFormat={fmtUsd0} />
          <LineChart x={curve.t} series={series.dd} time height={90} label="Drawdown below peak" yFormat={fmtUsd0} />
          <p className="dim">
            {int(curve.t.length)} settled trades; final <span className="num">{signedUsd(curve.pnl.at(-1))}</span>; max drawdown (engine){" "}
            <span className="num">{usd(maxDrawdown)}</span>.
          </p>
        </>
      )}
    </section>
  );
}
