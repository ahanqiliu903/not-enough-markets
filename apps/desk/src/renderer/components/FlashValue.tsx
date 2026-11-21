import { useValueFlash } from "../lib/flash";

/** A right-aligned tabular number that flashes green/red for 150ms when it changes. */
export function FlashValue({
  value,
  format = (v) => String(v),
}: {
  value: number | null;
  format?: (v: number) => string;
}) {
  const cls = useValueFlash(value);
  return <span className={`num ${cls}`}>{value === null ? "-" : format(value)}</span>;
}
