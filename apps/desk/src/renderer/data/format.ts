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
