import { EmptyState } from "../../components/states";

/** Screen 6 (placeholder, hidden until backtest data exists). */
export default function BacktestsScreen() {
  return (
    <EmptyState
      title="No backtests"
      reason="Backtests only reject ideas; a good-looking replay is not evidence. None have been exported yet."
      commands={["uv run nem replay --data data/nem.db", "uv run nem export"]}
    />
  );
}
