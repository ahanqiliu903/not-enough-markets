import { useEffect, useRef, useState } from "react";

export const FLASH_MS = 150;

export type FlashDirection = "up" | "down" | null;

export function flashDirection(prev: number | null, next: number | null): FlashDirection {
  if (prev === null || next === null || !Number.isFinite(prev) || !Number.isFinite(next)) {
    return null;
  }
  if (next > prev) return "up";
  if (next < prev) return "down";
  return null;
}

export function prefersReducedMotion(): boolean {
  try {
    // matchMedia is missing in some test environments.
    const mm = (globalThis as { matchMedia?: typeof globalThis.matchMedia }).matchMedia;
    return mm?.("(prefers-reduced-motion: reduce)").matches ?? false;
  } catch {
    return false;
  }
}

/**
 * Returns "flash-up" / "flash-down" for 150ms after a numeric value changes, else "".
 * Disabled under prefers-reduced-motion (the CSS also guards it).
 */
export function useValueFlash(value: number | null): string {
  const prev = useRef<number | null>(value);
  const [dir, setDir] = useState<FlashDirection>(null);
  useEffect(() => {
    const d = flashDirection(prev.current, value);
    prev.current = value;
    if (d === null || prefersReducedMotion()) return;
    setDir(d);
    const t = setTimeout(() => setDir(null), FLASH_MS);
    return () => clearTimeout(t);
  }, [value]);
  return dir === null ? "" : `flash-${dir}`;
}
