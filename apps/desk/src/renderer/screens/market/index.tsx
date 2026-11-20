import { EmptyState } from "../../components/states";

/** Screen 5 (placeholder). Replaced by the Market ticket. */
export default function MarketScreen() {
  return (
    <EmptyState
      title="No market data"
      reason="Order books and prices come from data the engine recorded (uv run nem run records as it trades). No recording is connected yet."
      commands={["uv run nem run", "uv run nem export"]}
    />
  );
}
