import { useQuery } from "@tanstack/react-query";
import { formatAge } from "../components/states";
import { useNow, useSnapshot } from "../data/hooks";
import { nem, queryKeys } from "../lib/api";
import { isMac } from "../lib/keys";

/**
 * Bottom status bar: the active source and the age of its newest `as_of`. The engine
 * heartbeat stays a placeholder until the Status screen (DESK-014) and says so.
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
  const age = ok?.newestAsOf != null ? formatAge(now.getTime() - Date.parse(ok.newestAsOf)) : "-";
  return (
    <footer className="status-bar" aria-label="Status bar">
      <span className="status-item" data-testid="status-source">
        <span className="dim">source</span> {sourceLabel} <span className="dim">age</span> {age}
      </span>
      <span className="status-item" data-testid="status-heartbeat">
        <span className="dim">engine</span> no heartbeat
      </span>
      <span className="status-spacer" />
      <span className="status-item dim">
        <kbd>{isMac() ? "Cmd" : "Ctrl"}+K</kbd> commands
      </span>
      <span className="status-item" data-testid="status-version">
        <span className="dim">NEM Desk</span> v{version.data ?? "..."}
      </span>
    </footer>
  );
}
