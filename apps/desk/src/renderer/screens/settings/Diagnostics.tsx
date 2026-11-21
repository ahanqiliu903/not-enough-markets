import { useMutation, useQuery } from "@tanstack/react-query";
import type { Diagnostics as Diag } from "@shared/diagnostics";
import { nem, queryKeys } from "../../lib/api";
import "./vps.css";

/**
 * DESK-020: what a bug report needs, redacted in main (sheet IDs, hosts, user names, home
 * and key paths become placeholders). "Copy diagnostics" has main write the same report,
 * as text, to the clipboard.
 */

function dbLine(db: Diag["db"]): string {
  if (db === null) return "none for the active source";
  if ("error" in db) return db.error;
  return `${db.path} (user_version ${db.schemaVersion}, ${(db.sizeBytes / 1024 / 1024).toFixed(1)} MB, ${db.series} series, ${db.feeds} feeds, newest ${db.newestTs ?? "-"})`;
}

export function Diagnostics() {
  const q = useQuery({ queryKey: queryKeys.diagnostics, queryFn: () => nem().data.diagnostics.get() });
  const copy = useMutation({ mutationFn: () => nem().data.diagnostics.copy() });
  const reveal = useMutation({ mutationFn: () => nem().data.diagnostics.revealLog() });

  return (
    <section className="section" aria-label="Diagnostics">
      <h2 className="section-title">Diagnostics</h2>
      <div className="row-wrap">
        <button type="button" onClick={() => copy.mutate()} disabled={copy.isPending}>
          Copy diagnostics
        </button>
        <button type="button" onClick={() => reveal.mutate()}>
          Show log file
        </button>
        <button type="button" onClick={() => void q.refetch()}>
          Refresh
        </button>
        {copy.isSuccess && (
          <span className={copy.data.ok ? "dim" : "down"} role="status">
            {copy.data.ok ? "Copied (private values replaced with placeholders)." : copy.data.message}
          </span>
        )}
      </div>
      {q.isPending ? (
        <p className="dim">Collecting...</p>
      ) : q.isError ? (
        <p className="dim">Diagnostics are only available inside NEM Desk.</p>
      ) : (
        <>
          <table className="grid" aria-label="Diagnostics summary">
            <tbody>
              <tr>
                <td>App</td>
                <td data-testid="diag-app">
                  {q.data.app.version}
                  {q.data.app.packaged ? "" : " (unpackaged)"} · Electron {q.data.app.electron} · {q.data.app.platform}/
                  {q.data.app.arch}
                </td>
              </tr>
              <tr>
                <td>Contract</td>
                <td data-testid="diag-contract">
                  {q.data.contract.expected} · DB user_version {q.data.contract.dbSchemaVersions.join(", ")}
                </td>
              </tr>
              <tr>
                <td>Log file</td>
                <td className="dim">{q.data.logFile}</td>
              </tr>
              <tr>
                <td>Database</td>
                <td className="dim">{dbLine(q.data.db)}</td>
              </tr>
              <tr>
                <td>Google Sheet</td>
                <td className="dim">
                  {q.data.sheets.configured ? `${q.data.sheets.portfolios.join(", ")}; last ${q.data.sheets.lastSync ?? "-"}` : "not configured"}
                </td>
              </tr>
              <tr>
                <td>VPS</td>
                <td className="dim">{q.data.vps.configured ? `last ${q.data.vps.lastSync ?? "-"}` : "not configured"}</td>
              </tr>
            </tbody>
          </table>
          <table className="grid" aria-label="Source loads">
            <tbody>
              {q.data.sources.map((s) => (
                <tr key={s.id}>
                  <td>{s.active ? "active" : ""}</td>
                  <td>
                    {s.label} <span className="dim">[{s.kind}]</span>
                  </td>
                  <td className="dim">{s.lastLoadAt ?? "not loaded yet"}</td>
                  <td className={s.errorCount === null || s.errorCount === 0 ? "dim" : "down"}>
                    {s.errorCount === null ? "" : `${s.errorCount} problem(s)`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <pre className="vps-detail" aria-label="Recent log lines" data-testid="diag-log">
            {q.data.recentLog.slice(-40).join("\n") || "(no log lines yet)"}
          </pre>
        </>
      )}
    </section>
  );
}
