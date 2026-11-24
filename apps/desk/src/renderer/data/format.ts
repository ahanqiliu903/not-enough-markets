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

/** What the pending (provisional) numbers are, shown wherever they are (contract 1.1). */
export const PENDING_NOTE = "not counted: Kalshi settles";

const PROVISIONAL_TEXT: Record<string, string> = {
  locked_win: "locked win",
  locked_loss: "locked loss",
  likely_win: "likely win",
  likely_loss: "likely loss",
  conflict: "conflict",
  unknown: "",
};

/**
 * An open trade's provisional result, e.g. "locked win (obs 56°F)", "likely loss (price)",
 * "conflict (obs 61°F vs price)"; "" when none or unknown. An unrecognised value shows as is.
 */
export function provisionalText(r: {
  provisional_result: string | null;
  provisional_basis: string | null;
  observed_extreme_f: number | null;
}): string {
  const status = r.provisional_result;
  if (status === null) return "";
  const label = PROVISIONAL_TEXT[status] ?? status.replace(/_/g, " ");
  if (label === "") return "";
  const obs = finite(r.observed_extreme_f) ? `obs ${int(r.observed_extreme_f)}°F` : "obs";
  const basis =
    r.provisional_basis === "observed" ? obs
    : r.provisional_basis === "price" ? "price"
    : r.provisional_basis === "both" ? `${obs} ${status === "conflict" ? "vs" : "+"} price`
    : r.provisional_basis ?? "";
  return basis === "" ? label : `${label} (${basis})`;
}

/** CSS class for a provisional result: wins up, losses down. */
export function provisionalClass(status: string | null): string {
  if (status === "locked_win" || status === "likely_win") return "up";
  if (status === "locked_loss" || status === "likely_loss") return "down";
  return "";
}

/**
 * Pending P&L of one kind with its count, e.g. "+$4.39 (6)"; NA when not computed (null).
 */
export function pendingPnl(pnl: number | null | undefined, n: number | null | undefined): string {
  if (!finite(pnl) || !finite(n)) return NA;
  return n > 0 ? `${signedUsd(pnl)} (${int(n)})` : "0";
}

/**
 * A row's pending results in one cell, e.g. "locked +$0.92 (1); likely +$4.39 (6); 1 conflict";
 * "" when nothing is pending, NA when not computed.
 */
export function pendingSummary(r: {
  pending_locked_pnl_usd: number | null;
  pending_likely_pnl_usd: number | null;
  pending_locked: number | null;
  pending_likely: number | null;
  pending_conflict: number | null;
}): string {
  if (!finite(r.pending_locked) || !finite(r.pending_likely)) return NA;
  const parts = [
    ...(r.pending_locked > 0 ? [`locked ${pendingPnl(r.pending_locked_pnl_usd, r.pending_locked)}`] : []),
    ...(r.pending_likely > 0 ? [`likely ${pendingPnl(r.pending_likely_pnl_usd, r.pending_likely)}`] : []),
    ...(finite(r.pending_conflict) && r.pending_conflict > 0 ? [`${int(r.pending_conflict)} conflict`] : []),
  ];
  return parts.join("; ");
}
