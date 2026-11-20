import { EmptyState } from "../../components/states";

/** Screen 2 (placeholder). Replaced by the Strategy detail ticket. */
export default function StrategyScreen() {
  return (
    <EmptyState
      title="No strategy selected"
      reason="Pick a strategy from Portfolios (Cmd/Ctrl+1) once a data source is connected. Strategy results come from the engine's export."
      commands={["make demo", "uv run nem export"]}
    />
  );
}
