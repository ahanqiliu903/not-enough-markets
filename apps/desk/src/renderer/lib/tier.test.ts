import { describe, expect, it } from "vitest";
import { TIER_LABELS, tier } from "./tier";

describe("tier()", () => {
  it("maps verdict edge to legendary regardless of edge", () => {
    expect(tier("edge", 2.5)).toBe("legendary");
    expect(tier("edge", -1)).toBe("legendary");
    expect(tier("edge", null)).toBe("legendary");
  });

  it("maps verdict no_edge to cursed regardless of edge", () => {
    expect(tier("no_edge", 3)).toBe("cursed");
    expect(tier("no_edge", -3)).toBe("cursed");
  });

  it("maps undecided with positive edge to rare", () => {
    expect(tier("undecided", 0.01)).toBe("rare");
    expect(tier("undecided", 5)).toBe("rare");
  });

  it("maps undecided with edge <= 0 to common", () => {
    expect(tier("undecided", 0)).toBe("common");
    expect(tier("undecided", -0)).toBe("common");
    expect(tier("undecided", -2)).toBe("common");
  });

  it("never upgrades on missing or non-finite edge", () => {
    expect(tier("undecided", null)).toBe("common");
    expect(tier("undecided", undefined)).toBe("common");
    expect(tier("undecided", Number.NaN)).toBe("common");
    expect(tier("undecided", Number.POSITIVE_INFINITY)).toBe("common");
  });

  it("uses the spec labels", () => {
    expect(TIER_LABELS).toEqual({
      common: "UNDECIDED",
      rare: "UNDECIDED+",
      legendary: "EDGE",
      cursed: "NO EDGE",
    });
  });
});
