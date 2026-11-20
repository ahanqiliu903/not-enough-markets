import type { ReactNode } from "react";

/**
 * Shared empty / stale / schema-error states (spec Section 5). Every screen uses these so
 * the user always learns why there is nothing to show and how to fix it.
 */

export function EmptyState({
  title,
  reason,
  commands = [],
  children,
}: {
  title: string;
  /** Why there is no data, in plain words. */
  reason: string;
  /** Shell commands that generate the missing data, e.g. ["make demo", "uv run nem export"]. */
  commands?: readonly string[];
  children?: ReactNode;
}) {
  return (
    <section className="state state-empty" role="status" aria-label={title}>
      <h2 className="state-title">{title}</h2>
      <p>{reason}</p>
      {commands.length > 0 && (
        <>
          <p className="dim">To generate it, run from the repo root:</p>
          <pre className="state-commands">
            {commands.map((c) => (
              <code key={c}>{c}</code>
            ))}
          </pre>
        </>
      )}
      {children}
    </section>
  );
}

export const DEFAULT_STALE_MS = 15 * 60 * 1000;

export function isStale(
  newest: Date | null,
  now: Date,
  thresholdMs: number = DEFAULT_STALE_MS,
): boolean {
  if (newest === null) return false;
  return now.getTime() - newest.getTime() > thresholdMs;
}

export function formatAge(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "unknown";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d`;
}

/** Renders nothing while data is fresh; a warning banner once it is older than threshold. */
export function StaleBanner({
  newest,
  now = new Date(),
  thresholdMs = DEFAULT_STALE_MS,
  source,
}: {
  newest: Date | null;
  now?: Date;
  thresholdMs?: number;
  source?: string;
}) {
  if (newest === null || !isStale(newest, now, thresholdMs)) return null;
  const age = formatAge(now.getTime() - newest.getTime());
  return (
    <div className="banner banner-stale" role="alert">
      <strong>STALE</strong> Newest data{source !== undefined ? ` from ${source}` : ""} is {age}{" "}
      old (newest {newest.toISOString()}; threshold {formatAge(thresholdMs)}). Numbers below may
      not reflect the engine now.
    </div>
  );
}

export function SchemaError({
  file,
  field,
  expectedVersion,
  foundVersion,
  detail,
}: {
  file: string;
  field: string;
  expectedVersion: string;
  foundVersion?: string;
  detail?: string;
}) {
  return (
    <section className="state state-error" role="alert" aria-label="Schema error">
      <h2 className="state-title">Schema error</h2>
      <dl className="kv">
        <dt>File</dt>
        <dd>
          <code>{file}</code>
        </dd>
        <dt>Field</dt>
        <dd>
          <code>{field}</code>
        </dd>
        <dt>Expected</dt>
        <dd>schema version {expectedVersion}</dd>
        {foundVersion !== undefined && (
          <>
            <dt>Found</dt>
            <dd>{foundVersion}</dd>
          </>
        )}
      </dl>
      {detail !== undefined && <p className="dim">{detail}</p>}
      <p className="dim">
        Nothing from this file is shown. Re-export with a matching NEM version, or update NEM
        Desk.
      </p>
    </section>
  );
}
