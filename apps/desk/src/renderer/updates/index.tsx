import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UpdateState } from "@shared/updates";
import { nem, queryKeys } from "../lib/api";
import "./updates.css";

/**
 * DESK-019 "update available" notice and its Settings section. Main does the checking
 * (GitHub Releases, GET only, at most daily); the renderer only reads main's state, so
 * polling here never touches the network.
 */

export function useUpdateState() {
  return useQuery({
    queryKey: queryKeys.updates,
    queryFn: () => nem().data.updates.get(),
    refetchInterval: 5 * 60_000,
  });
}

/** Status-bar item, shown only when a newer release was found. Errors stay silent here. */
export function UpdateNotice() {
  const q = useUpdateState();
  const last = q.data?.last;
  if (last?.ok !== true || !last.updateAvailable || last.latest === null) return null;
  return (
    <span className="status-item" data-testid="update-notice">
      <button
        type="button"
        className="update-link"
        onClick={() => void nem().data.updates.openRelease()}
        title={`Open the ${last.latest.tag} release page in your browser`}
      >
        update v{last.latest.version} available
      </button>
    </span>
  );
}

function describeState(s: UpdateState): string {
  const last = s.last;
  if (s.checking) return "Checking...";
  if (last === null) return "Not checked yet.";
  const when = new Date(last.at).toLocaleString();
  if (!last.ok) return `Last check failed (${when}): ${last.error.message}`;
  if (last.latest === null) return `No NEM Desk release published yet (checked ${when}).`;
  return last.updateAvailable
    ? `v${last.latest.version} is available (checked ${when}).`
    : `Up to date: newest release is v${last.latest.version} (checked ${when}).`;
}

/** Settings: automatic check toggle, "Check now", and the last result including errors. */
export function UpdateSettings() {
  const client = useQueryClient();
  const q = useUpdateState();
  const set = (s: UpdateState) => client.setQueryData(queryKeys.updates, s);
  const toggle = useMutation({ mutationFn: (on: boolean) => nem().data.updates.setEnabled(on), onSuccess: set });
  const check = useMutation({ mutationFn: () => nem().data.updates.check(), onSuccess: set });
  const s = q.data;
  const last = s?.last;
  return (
    <section className="section" aria-label="Updates">
      <h2 className="section-title">Updates</h2>
      <p className="dim">
        NEM Desk builds are unsigned and never update themselves. Once a day it asks GitHub Releases whether a newer
        version exists and, if so, links to its page; nothing is downloaded or installed.
      </p>
      {s === undefined ? (
        <p className="dim">{q.isError ? `Could not read update settings: ${String(q.error)}` : "Loading..."}</p>
      ) : (
        <>
          <div className="form-row">
            <label htmlFor="update-checks">Check daily</label>
            <input
              id="update-checks"
              type="checkbox"
              checked={s.enabled}
              onChange={(e) => toggle.mutate(e.target.checked)}
            />
            <span className="dim">{s.enabled ? "on" : "off"}: automatic check (Check now always works)</span>
          </div>
          <div className="form-row">
            <span>Version</span>
            <span className="num">v{s.current}</span>
            <button type="button" onClick={() => check.mutate()} disabled={check.isPending || s.checking}>
              Check now
            </button>
          </div>
          <p data-testid="update-result" role={last?.ok === false ? "alert" : undefined} className={last?.ok === false ? "down" : "dim"}>
            {check.isPending ? "Checking..." : describeState(s)}
          </p>
          {last?.ok === true && last.updateAvailable && last.latest !== null && (
            <button type="button" onClick={() => void nem().data.updates.openRelease()}>
              Open v{last.latest.version} release page
            </button>
          )}
        </>
      )}
    </section>
  );
}
