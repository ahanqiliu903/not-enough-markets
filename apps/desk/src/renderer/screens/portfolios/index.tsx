import { EmptyState } from "../../components/states";

/** Screen 1 (placeholder). Replaced by the Portfolios ticket. */
export default function PortfoliosScreen() {
  return (
    <EmptyState
      title="No portfolios yet"
      reason="NEM Desk has no data source connected, so there are no portfolios or verdicts to show. The desk reads exported engine results; it never computes them."
      commands={["make demo", "uv run nem export"]}
    />
  );
}
