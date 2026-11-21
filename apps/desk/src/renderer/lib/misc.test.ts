import { describe, expect, it } from "vitest";
import { DEFAULT_STALE_MS, formatAge, isStale } from "../components/states";
import { clampFontSize } from "../state/settings";
import { flashDirection } from "./flash";
import { fuzzyFilter, fuzzyScore } from "./fuzzy";

describe("fuzzy", () => {
  it("matches in-order subsequences only", () => {
    expect(fuzzyScore("ptf", "Portfolios")).not.toBeNull();
    expect(fuzzyScore("ftp", "Portfolios")).toBeNull();
  });
  it("ranks prefix/consecutive matches first", () => {
    const items = ["Trades", "Strategy", "Feeds and Status"];
    expect(fuzzyFilter(items, "st", (s) => s)[0]).toBe("Strategy");
    expect(fuzzyFilter(items, "", (s) => s)).toEqual(items);
  });
});

describe("stale", () => {
  const now = new Date("2026-01-01T12:00:00Z");
  it("uses a 15 minute default threshold", () => {
    expect(DEFAULT_STALE_MS).toBe(15 * 60 * 1000);
    expect(isStale(new Date("2026-01-01T11:50:00Z"), now)).toBe(false);
    expect(isStale(new Date("2026-01-01T11:40:00Z"), now)).toBe(true);
    expect(isStale(null, now)).toBe(false);
  });
  it("formats ages", () => {
    expect(formatAge(42_000)).toBe("42s");
    expect(formatAge(20 * 60_000)).toBe("20m");
    expect(formatAge(-1)).toBe("unknown");
  });
});

describe("settings", () => {
  it("clamps font size to 11-16", () => {
    expect(clampFontSize(8)).toBe(11);
    expect(clampFontSize(20)).toBe(16);
    expect(clampFontSize(13.4)).toBe(13);
    expect(clampFontSize(Number.NaN)).toBe(13);
  });
});

describe("flash", () => {
  it("knows direction", () => {
    expect(flashDirection(1, 2)).toBe("up");
    expect(flashDirection(2, 1)).toBe("down");
    expect(flashDirection(1, 1)).toBeNull();
    expect(flashDirection(null, 1)).toBeNull();
  });
});
