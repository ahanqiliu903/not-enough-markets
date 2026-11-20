import { useQuery } from "@tanstack/react-query";
import { nem, queryKeys } from "../lib/api";
import { isMac } from "../lib/keys";

/**
 * Bottom status bar. Data source/age and engine heartbeat are placeholders until the data
 * layer (DESK-003) provides them; they say so instead of showing made-up values.
 */
export function StatusBar() {
  const version = useQuery({
    queryKey: queryKeys.appVersion,
    queryFn: () => nem().appVersion(),
    staleTime: Infinity,
  });
  return (
    <footer className="status-bar" aria-label="Status bar">
      <span className="status-item" data-testid="status-source">
        <span className="dim">source</span> none <span className="dim">age</span> -
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
