import { useQuery } from "@tanstack/react-query";
import { formatAge } from "../components/states";
import { useNow, useSnapshot } from "../data/hooks";
import { nem, queryKeys } from "../lib/api";
import { isMac } from "../lib/keys";
import { UpdateNotice } from "../updates";

/**
 * Bottom status bar: the active source and the age of its newest `as_of`, and the engine's
 * own health verdict from status.json (DESK-014) with the newest heartbeat's age.
 */
export function StatusBar() {
  const version = useQuery({
    queryKey: queryKeys.appVersion,
    queryFn: () => nem().appVersion(),
    staleTime: Infinity,
  });
  const snap = useSnapshot();
  const now = useNow();
  const ok = snap.data?.state === "ok" ? snap.data.snapshot : null;
  const sourceLabel =
    ok !== null ? ok.source.label : snap.data?.state === "error" ? `${snap.data.source.label} (unavailable)` : "none";
  const st = ok?.status ?? null;
  const beats = (st?.processes ?? []).map((p) => p.last_heartbeat_at).filter((t): t is string => t !== null);
  // Contract timestamps are fixed-format UTC with Z, so the newest sorts last as text.
  const newestBeat = beats.length > 0 ? beats.reduce((a, b) => (b > a ? b : a)) : null;
  const engine =
    st === null
      ? "no status.json"
      : `${st.healthy ? "healthy" : "UNHEALTHY"}, heartbeat ${newestBeat === null ? "never" : `${formatAge(now.getTime() - Date.parse(newestBeat))} ago`}`;
  const age = ok?.newestAsOf != null ? formatAge(now.getTime() - Date.parse(ok.newestAsOf)) : "-";
  return (
    <footer className="status-bar" aria-label="Status bar">
      <span className="status-item" data-testid="status-source">
        <span className="dim">source</span> {sourceLabel} <span className="dim">age</span> {age}
      </span>
      <span className="status-item" data-testid="status-heartbeat">
        <span className="dim">engine</span> {engine}
      </span>
      <span className="status-spacer" />
      <UpdateNotice />
      <span className="status-item dim">
        <kbd>{isMac() ? "Cmd" : "Ctrl"}+K</kbd> commands
      </span>
      <span className="status-item" data-testid="status-version">
        <span className="dim">NEM Desk</span> v{version.data ?? "..."}
      </span>
    </footer>
  );
}
