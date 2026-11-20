import { EmptyState } from "../../components/states";

/** Screen 4 (placeholder). Replaced by the Trades ticket. */
export default function TradesScreen() {
  return (
    <EmptyState
      title="No trades"
      reason="No data source is connected, so there are no paper trades to list."
      commands={["make demo", "uv run nem export"]}
    />
  );
}
