import { EmptyState } from "../../components/states";

/** Screen 3 (placeholder). Replaced by the Compare ticket. */
export default function CompareScreen() {
  return (
    <EmptyState
      title="Nothing to compare"
      reason="Select strategies and press c to add them here. That needs a connected data source with at least one portfolio."
      commands={["make demo", "uv run nem export"]}
    />
  );
}
