import type { BacktestFeeRate, BacktestRun, Snapshot, StrategyRow, VariantMetrics } from "@shared/data";
import { PixelIcon } from "../../components/PixelIcon";
import { TierBadge } from "../../components/TierBadge";
import { IntervalChart } from "../../data/charts";
import { cents, int, ts } from "../../data/format";
import { useNav } from "../../state/nav";
import { chosenVariant, intervalsDisjoint, paperRow, showValue } from "./model";
import { MetricsTable, RunStatusTag, SweepHeatmap, VariantsTable, WindowsBar } from "./parts";

const TEST_NOTE =
  "Test (held-out) metrics exist for the chosen variant only, by design: showing every variant's test result would invite choosing on test data.";

function feeText(r: BacktestFeeRate | null): string {
  if (r === null) return "-";
  return `${r.rate === null ? "unknown" : r.rate.toFixed(4)} (${r.source}: ${r.detail})`;
}

/** Contract 1.1 manifest: what the result depends on, as saved by the engine. */
function Reproducibility({ run }: { run: BacktestRun }) {
  if (run.dataset === undefined) {
    return (
      <section className="panel-box" aria-label="Reproducibility">
        <h3>Reproducibility</h3>
        <p className="dim">Saved before contract 1.1: no manifest, so this run can't be rerun.</p>
      </section>
    );
  }
  const fees = (run.fees ?? []).filter((f) => f.strategy === run.strategy);
  return (
    <section className="panel-box" aria-label="Reproducibility">
      <h3>Reproducibility</h3>
      {run.hypothesis ? <p>hypothesis: {run.hypothesis}</p> : null}
      {fees.map((f) => (
        <p key={f.strategy}>
          taker fee <b>{feeText(f.taker_fee_rate)}</b>
          {f.maker_fee_rate === null ? null : <> · maker fee {feeText(f.maker_fee_rate)}</>}
        </p>
      ))}
      <p className="dim">
        data <code>{run.dataset.path}</code> sha256 <code>{run.dataset.sha256.slice(0, 12)}</code> · engine{" "}
        {run.engine?.version ?? "-"} {run.engine?.git_revision?.slice(0, 10) ?? ""} · rerun:{" "}
        <code>nem rerun {run.file}</code>
      </p>
      {run.notes ? <p className="dim">notes: {run.notes}</p> : null}
    </section>
  );
}

export function RunDetail({ run, snapshot }: { run: BacktestRun; snapshot: Snapshot }) {
  const navigate = useNav((s) => s.navigate);
  const chosen = chosenVariant(run);
  return (
    <div className="screen-stack" aria-label={`Backtest ${run.run_id}`}>
      <div className="card-head">
        <RunStatusTag status={run.status} />
        <PixelIcon name="backtests" className="card-icon" />
        <h2 className="card-title pixel-font">{run.strategy}</h2>
        <span className="dim">
          {run.portfolio} / {run.series}
        </span>
        <span className="dim">created {ts(run.created_at)}</span>
        <span className="spacer" />
        <button type="button" onClick={() => navigate("backtests")}>
          All runs
        </button>
        <button type="button" onClick={() => navigate("strategy", { portfolio: run.portfolio, strategy: run.strategy })}>
          Strategy
        </button>
      </div>
      <p className="dim">
        Run <code>{run.run_id}</code> from <code>{run.file}</code>. Backtests only reject ideas: a good-looking one is not
        evidence of an edge.
      </p>

      <div className="detail-grid">
        <section className="panel-box" aria-label="Chosen variant">
          <h3>Chosen variant</h3>
          {chosen === null ? (
            <p>No variant chosen ({run.status}): nothing here to paper trade from this sweep.</p>
          ) : (
            <>
              <p>
                <b>{chosen.name}</b>{" "}
                {Object.entries(chosen.overrides).map(([k, v]) => (
                  <code key={k} className="override">
                    {k}={showValue(v)}
                  </code>
                ))}
              </p>
              <p>
                stability score (train) <b className="num">{cents(run.score_cents)}</b>{" "}
                <span className="dim">per contract; min_trades {int(run.min_trades)}</span>
              </p>
            </>
          )}
          <p>
            fill estimate: <b>{run.fill_estimate === null ? "not estimated" : "present"}</b>{" "}
            <span className="dim">
              {run.fill_estimate === null
                ? "(contract 1.0: fills walk the recorded order book; no pessimistic / optimistic range is modelled yet)"
                : JSON.stringify(run.fill_estimate)}
            </span>
          </p>
        </section>
        <section className="panel-box" aria-label="Windows">
          <h3>Train and test windows</h3>
          <WindowsBar run={run} />
        </section>
      </div>

      <Reproducibility run={run} />

      <section className="panel-box" aria-label="Engine reasons">
        <h3>Engine reasons</h3>
        <ul className="plain-list reasons">
          {run.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      </section>

      <section className="panel-box" aria-label="Grid">
        <h3>Sweep</h3>
        <table className="grid" aria-label="Grid parameters">
          <thead>
            <tr>
              <th>Parameter</th>
              <th>Values</th>
            </tr>
          </thead>
          <tbody>
            {run.grid.map((g) => (
              <tr key={g.path}>
                <td>
                  <code>{g.path}</code>
                </td>
                <td>
                  {g.values.map((v, i) => {
                    const isChosen = chosen !== null && chosen.index[run.grid.indexOf(g)] === i;
                    return (
                      <span key={i} className={`grid-value${isChosen ? " grid-value-chosen" : ""}`}>
                        {showValue(v)}
                      </span>
                    );
                  })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <SweepHeatmap run={run} />
        <VariantsTable run={run} />
      </section>

      <section className="panel-box" aria-label="Train vs test">
        <h3>Train vs test (chosen variant)</h3>
        <MetricsTable
          label="Train vs test metrics"
          cols={[
            { label: "Train", metrics: chosen?.train ?? null, missing: "no variant chosen" },
            { label: "Test (held out)", metrics: chosen?.test ?? null, missing: chosen === null ? "no variant chosen" : "not run" },
          ]}
        />
        <p className="dim">{TEST_NOTE}</p>
      </section>

      <VsPaper run={run} chosenTest={chosen?.test ?? null} chosenTrain={chosen?.train ?? null} paper={paperRow(snapshot, run)} />
    </div>
  );
}

function edgeDomain(items: ({ edge_cents: number | null; edge_lo_cents: number | null; edge_hi_cents: number | null } | null)[]): [number, number] {
  const vals = [0];
  for (const m of items) {
    if (m === null) continue;
    for (const v of [m.edge_cents, m.edge_lo_cents, m.edge_hi_cents]) if (v !== null) vals.push(v);
  }
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const pad = Math.max(1, (hi - lo) * 0.15);
  return [lo - pad, hi + pad];
}

const fmtCents1 = (v: number) => cents(v, 1);

/** DESK-017: the same strategy in backtest (train, test), paper (strategies.csv) and live. */
function VsPaper({
  run,
  chosenTrain,
  chosenTest,
  paper,
}: {
  run: BacktestRun;
  chosenTrain: VariantMetrics | null;
  chosenTest: VariantMetrics | null;
  paper: StrategyRow | null;
}) {
  const domain = edgeDomain([chosenTrain, chosenTest, paper]);
  const diverges =
    chosenTest !== null &&
    paper !== null &&
    intervalsDisjoint(
      { lo: chosenTest.edge_lo_cents, hi: chosenTest.edge_hi_cents },
      { lo: paper.edge_lo_cents, hi: paper.edge_hi_cents },
    );
  const rows: { label: string; m: { edge_cents: number | null; edge_lo_cents: number | null; edge_hi_cents: number | null; trades: number } | null; why: string }[] = [
    { label: "Backtest train", m: chosenTrain, why: "no variant chosen" },
    { label: "Backtest test", m: chosenTest, why: chosenTrain === null ? "no variant chosen" : "not run" },
    { label: "Paper", m: paper, why: `${run.portfolio}/${run.strategy} is not in this source's strategies.csv` },
  ];
  return (
    <section className="panel-box" aria-label="Backtest vs paper vs live">
      <h3>Backtest vs paper vs live</h3>
      {diverges && (
        <div className="banner banner-diverge" role="alert">
          <strong>DIVERGES</strong> The paper edge interval and the backtest test edge interval do not overlap. Check that
          paper runs the chosen settings, and the fill and fee assumptions.
        </div>
      )}
      <div className="vs-edges">
        {rows.map((r) => (
          <div key={r.label} className="vs-edge-row" aria-label={`${r.label} edge`}>
            <span className="vs-edge-label">
              {r.label}
              {r.m !== null && <span className="dim"> n {int(r.m.trades)}</span>}
            </span>
            {r.m === null ? (
              <span className="dim">{r.why}</span>
            ) : (
              <IntervalChart
                label={`${r.label} edge interval vs zero`}
                value={r.m.edge_cents}
                lo={r.m.edge_lo_cents}
                hi={r.m.edge_hi_cents}
                reference={r.m.edge_cents === null ? null : 0}
                referenceLabel="zero"
                domain={domain}
                format={fmtCents1}
              />
            )}
          </div>
        ))}
        <div className="vs-edge-row" aria-label="Live edge">
          <span className="vs-edge-label">Live</span>
          <span className="dim">live: not implemented (NEM trades paper only)</span>
        </div>
      </div>
      <MetricsTable
        label="Backtest vs paper metrics"
        cols={[
          { label: "Backtest train", metrics: chosenTrain, missing: "no variant chosen" },
          { label: "Backtest test", metrics: chosenTest, missing: chosenTrain === null ? "no variant chosen" : "not run" },
          { label: "Paper (now)", metrics: paper, missing: "no paper row" },
          { label: "Live", metrics: null, missing: "live: not implemented" },
        ]}
      />
      {paper !== null && (
        <p>
          paper verdict <TierBadge verdict={paper.verdict} edgeCents={paper.edge_cents} />{" "}
          <span className="dim">status {paper.status}; {int(paper.open)} open</span>
        </p>
      )}
      <p className="dim">
        All values are the engine&apos;s: backtest from {run.file}, paper from strategies.csv. Paper runs whatever settings the
        portfolio file has now, which may not be the chosen variant, and its trades may overlap the backtest windows.
      </p>
    </section>
  );
}
