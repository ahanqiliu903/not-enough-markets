/**
 * Display formatting of the engine's numbers. Only formatting: no statistic is derived.
 * Null always renders as "-" (the contract's "no value yet"), never as 0.
 */

export const NA = "-";

function finite(v: number | null | undefined): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

export function pct(v: number | null | undefined, digits = 2): string {
  return finite(v) ? `${(v * 100).toFixed(digits)}%` : NA;
}

export function cents(v: number | null | undefined, digits = 2): string {
  return finite(v) ? `${v.toFixed(digits)}c` : NA;
}

export function usd(v: number | null | undefined, digits = 2): string {
  if (!finite(v)) return NA;
  const s = `$${Math.abs(v).toFixed(digits)}`;
  return v < 0 ? `-${s}` : s;
}

/** Signed dollars, for P&L: "+$1.20", "-$0.40". */
export function signedUsd(v: number | null | undefined, digits = 2): string {
  if (!finite(v)) return NA;
  return v > 0 ? `+${usd(v, digits)}` : usd(v, digits);
}

export function num(v: number | null | undefined, digits?: number): string {
  if (!finite(v)) return NA;
  return digits === undefined ? String(v) : v.toFixed(digits);
}

export function int(v: number | null | undefined): string {
  return finite(v) ? v.toLocaleString("en-US") : NA;
}

/** "lo .. hi" for an interval; NA unless both ends exist. */
export function range(lo: string, hi: string): string {
  return lo === NA || hi === NA ? NA : `${lo} .. ${hi}`;
}

/** Contract timestamps (UTC, Z) shown as "2026-10-02 18:35:00Z". */
export function ts(v: string | null | undefined): string {
  if (v === null || v === undefined || v === "") return NA;
  return v.replace("T", " ");
}

/** CSS class for a signed value: up / down / none. */
export function signClass(v: number | null | undefined): string {
  if (!finite(v) || v === 0) return "";
  return v > 0 ? "up" : "down";
}

/** Label for the engine's mark-to-market values (contract 1.1), shown wherever they are. */
export const MARK_LABEL = "liquidation value at best bids, after exit fee";

/** "+$1.20", "+$1.20 (2 unmarked)", "-$4.50 (5 no bids at $0)"; NA when the engine gave no value. */
export function unrealized(
  pnl: number | null | undefined,
  unmarked: number | null | undefined,
  noBid?: number | null,
): string {
  const v = signedUsd(pnl);
  if (v === NA) return v;
  const notes = [
    ...(finite(unmarked) && unmarked > 0 ? [`${int(unmarked)} unmarked`] : []),
    ...(finite(noBid) && noBid > 0 ? [`${int(noBid)} no bids at $0`] : []),
  ];
  return notes.length > 0 ? `${v} (${notes.join("; ")})` : v;
}

/**
 * Flag for an open trade the recorded bids don't fully cover: the engine valued the rest
 * at $0 (`mark_contracts` < `contracts`). "" when fully covered or unmarked.
 */
export function noBidFlag(markContracts: number | null | undefined, contracts: number): string {
  if (!finite(markContracts) || markContracts >= contracts) return "";
  return `no bids: ${int(contracts - markContracts)} at $0 (pending loss)`;
}

/** What excluding a trade means, shown wherever excluded trades are (contract 1.1). */
export const EXCLUDED_NOTE = "excluded (nem trade exclude): left out of statistics, P&L and equity";

/** "excluded: <reason>" for a trade the engine excluded from statistics; "" when counted. */
export function excludedFlag(r: { excluded: boolean | null; exclude_reason: string | null }): string {
  if (r.excluded !== true) return "";
  return `excluded: ${r.exclude_reason ?? "no reason given"}`;
}

/** Trades excluded from statistics, e.g. "2"; NA when an older file has no count. */
export function excludedCount(v: number | null | undefined): string {
  return finite(v) ? int(v) : NA;
}

/** Contracts valued at $0 for lack of bids, e.g. "10 (pending loss)"; NA when none known. */
export function noBidContracts(v: number | null | undefined): string {
  if (!finite(v)) return NA;
  return v > 0 ? `${int(v)} (pending loss)` : "0";
}
