import { EmptyState } from "../../components/states";

/** Screen 7 (placeholder). Replaced by the Feeds and Status ticket. */
export default function StatusScreen() {
  return (
    <EmptyState
      title="No engine status"
      reason="Engine heartbeat, feed freshness and halts appear here once a data source is connected."
      commands={["uv run nem status", "uv run nem export"]}
    />
  );
}
