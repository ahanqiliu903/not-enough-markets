import type { DataError, Snapshot, StatusReport } from "@shared/data";
import { EmptyState, formatAge } from "../../components/states";
import { int, ts } from "../../data/format";
import { DataErrors, SourceGate } from "../../data/states";
import { HaltControls } from "../../halt";
import { useNav } from "../../state/nav";
import "./status.css";

const STATUS_COMMANDS = ["uv run nem report", "uv run nem status --json > status.json"] as const;

/** Screen 7 (DESK-014): engine health from status.json (`nem status --json`), read-only. */
export default function StatusScreen() {
  return (
    <SourceGate emptyTitle="No engine status" emptyReason="Engine heartbeats and halts come from status.json.">
      {(s) => <Status snapshot={s} />}
    </SourceGate>
  );
}

function statusErrors(errors: readonly DataError[]): DataError[] {
  return errors.filter((e) => e.file === "status.json" || e.file.endsWith("/status.json"));
}

function Status({ snapshot }: { snapshot: Snapshot }) {
  const errors = statusErrors(snapshot.errors);
  const st = snapshot.status;
  return (
    <div className="screen-stack">
      {errors.length > 0 && <DataErrors errors={errors} />}
      {st === null ? (
        errors.length === 0 && (
          <EmptyState
            title="No engine status"
            reason="This source has no status.json. `nem report` writes out/status.json; `nem status --json` prints the same document."
            commands={STATUS_COMMANDS}
          />
        )
      ) : (
        <StatusReportView st={st} />
      )}
      <HaltControls />
      <FeedsNote />
    </div>
  );
}

/** "3m before as_of": how old a timestamp was when the engine wrote the report. */
function ageAt(asOf: string, t: string | null): string {
  if (t === null) return "never";
  return `${formatAge(Date.parse(asOf) - Date.parse(t))} before report`;
}

const STATE_TEXT: Record<StatusReport["processes"][number]["state"], string> = {
  ok: "OK",
  failing: "FAILING",
  stale: "STALE",
  never: "NEVER",
};

function StatusReportView({ st }: { st: StatusReport }) {
  const navigate = useNav((s) => s.navigate);
  return (
    <>
      <div className="card-head">
        <span className={`tag health health-${st.healthy ? "ok" : "bad"}`} data-healthy={st.healthy}>
          {st.healthy ? "HEALTHY" : "UNHEALTHY"}
        </span>
        <span className="dim">
          report as of {ts(st.as_of)} (engine: every expected process is ok or failing, i.e. alive)
        </span>
      </div>

      <section className="panel-box" aria-label="Processes">
        <h3>Processes</h3>
        {st.processes.length === 0 ? (
          <p className="dim">No process has sent a heartbeat yet (`uv run nem run` sends them).</p>
        ) : (
          <table className="grid" aria-label="Processes">
            <thead>
              <tr>
                <th>Process</th>
                <th>State</th>
                <th>Last heartbeat</th>
                <th>Age</th>
                <th>Expected</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {st.processes.map((p) => (
                <tr key={p.name}>
                  <td>{p.name}</td>
                  <td>
                    <span className={`proc-state proc-${p.state}`} data-state={p.state}>
                      {STATE_TEXT[p.state]}
                    </span>
                  </td>
                  <td>{ts(p.last_heartbeat_at)}</td>
                  <td className="dim">{ageAt(st.as_of, p.last_heartbeat_at)}</td>
                  <td>{p.expected ? "yes" : <span className="dim">no</span>}</td>
                  <td className="dim">{p.detail ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel-box" aria-label="Halts">
        <h3>Halts</h3>
        {st.halts.length === 0 ? (
          <p className="dim">No halts.</p>
        ) : (
          <table className="grid" aria-label="Halts">
            <thead>
              <tr>
                <th>Scope</th>
                <th>Reason</th>
                <th>Since</th>
              </tr>
            </thead>
            <tbody>
              {st.halts.map((h) => (
                <tr key={h.scope}>
                  <td>
                    <code>{h.scope}</code>
                  </td>
                  <td>{h.reason}</td>
                  <td>{ts(h.since)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="dim">Read-only. On the engine host: `uv run nem resume SCOPE` clears a halt.</p>
      </section>

      <section className="panel-box" aria-label="Strategy activity">
        <h3>Strategy activity</h3>
        {st.strategies.length === 0 ? (
          <p className="dim">No strategies in the engine's portfolio files.</p>
        ) : (
          <div className="grid-wrap">
            <table className="grid" aria-label="Strategy activity">
              <thead>
                <tr>
                  <th>Portfolio</th>
                  <th>Strategy</th>
                  <th>Series</th>
                  <th>Status</th>
                  <th>Halted by</th>
                  <th className="num">Open</th>
                  <th className="num">Settled</th>
                  <th>Last signal</th>
                </tr>
              </thead>
              <tbody>
                {st.strategies.map((s) => (
                  <tr
                    key={`${s.portfolio}/${s.strategy}`}
                    className="selectable"
                    onClick={() => navigate("strategy", { portfolio: s.portfolio, strategy: s.strategy })}
                  >
                    <td>{s.portfolio}</td>
                    <td>{s.strategy}</td>
                    <td>{s.series}</td>
                    <td>{s.status}</td>
                    <td>{s.halted_by === null ? <span className="dim">-</span> : <code className="down">{s.halted_by}</code>}</td>
                    <td className="num">{int(s.open)}</td>
                    <td className="num">{int(s.settled)}</td>
                    <td>
                      {ts(s.last_signal_at)} <span className="dim">{s.last_signal_at === null ? "" : `(${ageAt(st.as_of, s.last_signal_at)})`}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

/** Contract 1.0 has no feed freshness in status.json; say so rather than invent it. */
function FeedsNote() {
  return (
    <section className="panel-box" aria-label="Feeds">
      <h3>Feeds</h3>
      <p className="dim">
        Feed age vs max_age is not in status.json (contract 1.0), so it is not shown here. Feed values with their known_at
        times are in nem.db (feed_values) and appear on the Market screen when a database is connected.
      </p>
    </section>
  );
}
