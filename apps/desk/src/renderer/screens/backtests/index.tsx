import { useState } from "react";
import type { BacktestRun, DataError, Snapshot } from "@shared/data";
import { EmptyState } from "../../components/states";
import { ts } from "../../data/format";
import { DataErrors, SourceGate } from "../../data/states";
import { useKeys } from "../../lib/keys";
import { useNav } from "../../state/nav";
import { RunDetail } from "./RunDetail";
import { RunStatusTag } from "./parts";
import "./backtests.css";

const SUGGEST = "uv run nem suggest PORTFOLIO STRATEGY --data data/nem.db --grid PATH=V1,V2 --save";

/** Screen 6 (DESK-015..017): `nem suggest --save` runs, one sweep each. */
export default function BacktestsScreen() {
  return (
    <SourceGate emptyTitle="No backtests" emptyReason="Backtest runs come from `nem suggest --save` (out/backtests/).">
      {(s) => <Backtests snapshot={s} />}
    </SourceGate>
  );
}

function backtestErrors(errors: readonly DataError[]): DataError[] {
  return errors.filter((e) => e.file.includes("backtests/") || e.file.endsWith("backtests"));
}

function Backtests({ snapshot }: { snapshot: Snapshot }) {
  const params = useNav((s) => s.params);
  const errors = backtestErrors(snapshot.errors);
  const runId = params["run"];
  if (runId !== undefined) {
    const run = snapshot.backtests.find((r) => r.run_id === runId);
    if (run !== undefined) return <RunDetail run={run} snapshot={snapshot} />;
  }
  return (
    <div className="screen-stack">
      {errors.length > 0 && <DataErrors errors={errors} />}
      {runId !== undefined && <p className="down">Run {runId} is not in this source.</p>}
      {snapshot.backtests.length === 0 ? (
        <EmptyState
          title="No backtests"
          reason="Backtests only reject ideas; a good-looking replay is not evidence. This source has no saved runs in out/backtests/."
          commands={[SUGGEST]}
        />
      ) : (
        <RunList runs={snapshot.backtests} />
      )}
    </div>
  );
}

function RunList({ runs }: { runs: readonly BacktestRun[] }) {
  const navigate = useNav((s) => s.navigate);
  // Newest first: run ids and created_at are fixed-format UTC, so they sort as text.
  const sorted = [...runs].sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
  const [sel, setSel] = useState(0);
  const selected = sorted[Math.min(sel, sorted.length - 1)];
  const open = (r: BacktestRun) => navigate("backtests", { run: r.run_id });
  useKeys({
    j: () => setSel((i) => Math.min(sorted.length - 1, i + 1)),
    k: () => setSel((i) => Math.max(0, i - 1)),
    Enter: () => {
      if (selected === undefined) return false;
      open(selected);
    },
  });
  return (
    <section className="panel-box" aria-label="Backtest runs">
      <h3>Runs</h3>
      <div className="grid-wrap">
        <table className="grid" aria-label="Backtest runs">
          <thead>
            <tr>
              <th>Created</th>
              <th>Portfolio</th>
              <th>Strategy</th>
              <th>Series</th>
              <th>Status</th>
              <th>Run</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr
                key={r.file}
                className={`selectable${selected?.file === r.file ? " selected" : ""}`}
                aria-selected={selected?.file === r.file}
                onClick={() => open(r)}
              >
                <td>{ts(r.created_at)}</td>
                <td>{r.portfolio}</td>
                <td>{r.strategy}</td>
                <td>{r.series}</td>
                <td>
                  <RunStatusTag status={r.status} />
                </td>
                <td className="dim">{r.run_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="dim">j / k to move, Enter to open. A suggested run is a starting point for paper trading, not evidence of an edge.</p>
    </section>
  );
}
