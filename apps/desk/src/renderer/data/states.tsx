import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { EXPECTED_VERSION, type DataError, type Snapshot } from "@shared/data";
import { EmptyState, SchemaError, StaleBanner } from "../components/states";
import { nem } from "../lib/api";
import { useNow, useSnapshot, useSourcesState } from "./hooks";
import "./data.css";

/** Commands that produce the files the desk reads. */
export const DATA_COMMANDS = ["make demo", "uv run nem report"] as const;
export const DATA_HOWTO =
  "NEM Desk reads what the engine exports: `uv run nem report` with a csv reporter writes out/<portfolio>/{summary,strategies,trades}.csv; `uv run nem summary --json > summary.json` adds calibration and signal reasons.";

/** Buttons to connect a source: the bundled sample data, or a folder of engine output. */
export function ConnectSource() {
  const client = useQueryClient();
  const state = useSourcesState();
  const [message, setMessage] = useState<string | null>(null);
  const done = (r: { ok: boolean; message?: string }) => {
    setMessage(r.ok || r.message === "cancelled" ? null : (r.message ?? null));
    void client.invalidateQueries();
  };
  const sample = useMutation({ mutationFn: () => nem().sources.addSample(), onSuccess: done });
  const folder = useMutation({ mutationFn: () => nem().sources.addFolder(), onSuccess: done });
  return (
    <div className="row-wrap connect-source">
      {state.data?.sampleAvailable === true && (
        <button type="button" onClick={() => sample.mutate()} disabled={sample.isPending}>
          Load sample data
        </button>
      )}
      <button type="button" onClick={() => folder.mutate()} disabled={folder.isPending}>
        Add folder...
      </button>
      {message !== null && (
        <span role="alert" className="down">
          {message}
        </span>
      )}
    </div>
  );
}

/** One error in full as a SchemaError (file, field, expected version), the rest listed. */
export function DataErrors({ errors }: { errors: readonly DataError[] }) {
  const [first, ...rest] = errors;
  if (first === undefined) return null;
  return (
    <div className="data-errors">
      <SchemaError
        file={first.file}
        field={first.field ?? "(file)"}
        expectedVersion={first.expected ?? EXPECTED_VERSION}
        {...(first.found !== undefined ? { foundVersion: first.found } : {})}
        detail={first.message}
      />
      {rest.length > 0 && (
        <ul className="plain-list error-list" aria-label="More data errors">
          {rest.map((e, i) => (
            <li key={`${e.file}:${e.row ?? ""}:${e.field ?? ""}:${i}`}>
              <code>{e.file}</code> {e.field !== undefined && <code>{e.field}</code>}{" "}
              <span className="dim">{e.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Loads the active source and handles every non-data state (no source, unreadable source,
 * loading); renders `children` with the snapshot plus the stale banner otherwise.
 */
export function SourceGate({
  emptyTitle,
  emptyReason,
  children,
}: {
  emptyTitle: string;
  emptyReason: string;
  children: (snapshot: Snapshot) => ReactNode;
}) {
  const snap = useSnapshot();
  const now = useNow();
  if (snap.isPending) return <p className="dim">Loading data...</p>;
  if (snap.isError) {
    return (
      <EmptyState title="Could not read the data source" reason={String(snap.error)} commands={DATA_COMMANDS}>
        <ConnectSource />
      </EmptyState>
    );
  }
  const r = snap.data;
  if (r.state === "none") {
    return (
      <EmptyState title={emptyTitle} reason={`${emptyReason} No data source is connected. ${DATA_HOWTO}`} commands={DATA_COMMANDS}>
        <ConnectSource />
      </EmptyState>
    );
  }
  if (r.state === "error") {
    return (
      <EmptyState
        title="Data source unavailable"
        reason={`${r.source.label} (${r.source.path}): ${r.errors.map((e) => e.message).join("; ")}`}
        commands={DATA_COMMANDS}
      >
        <ConnectSource />
      </EmptyState>
    );
  }
  const s = r.snapshot;
  return (
    <>
      <StaleBanner newest={s.newestAsOf !== null ? new Date(s.newestAsOf) : null} now={now} source={s.source.label} />
      {children(s)}
    </>
  );
}
