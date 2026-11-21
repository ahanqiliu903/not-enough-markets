/**
 * Verdict tiers (spec Section 4). The verdict comes from the engine (`nem summary` /
 * export); this only maps it to a display tier and never recomputes statistics.
 *
 * | tier      | rule                              | label       |
 * |-----------|-----------------------------------|-------------|
 * | common    | undecided and edge_cents <= 0     | UNDECIDED   |
 * | rare      | undecided and edge_cents > 0      | UNDECIDED+  |
 * | legendary | verdict edge                      | EDGE        |
 * | cursed    | verdict no_edge                   | NO EDGE     |
 */

export type Verdict = "edge" | "no_edge" | "undecided";
export type Tier = "common" | "rare" | "legendary" | "cursed";

export const TIER_LABELS: Record<Tier, string> = {
  common: "UNDECIDED",
  rare: "UNDECIDED+",
  legendary: "EDGE",
  cursed: "NO EDGE",
};

/** CSS custom property holding each tier's color. */
export const TIER_COLOR_VAR: Record<Tier, string> = {
  common: "var(--tier-common)",
  rare: "var(--tier-rare)",
  legendary: "var(--tier-legendary)",
  cursed: "var(--tier-cursed)",
};

/**
 * Missing or non-finite edge on an undecided verdict is shown as `common`: we never
 * upgrade a strategy's tier on data we don't have.
 */
export function tier(verdict: Verdict, edgeCents: number | null | undefined): Tier {
  switch (verdict) {
    case "edge":
      return "legendary";
    case "no_edge":
      return "cursed";
    case "undecided":
      return typeof edgeCents === "number" && Number.isFinite(edgeCents) && edgeCents > 0
        ? "rare"
        : "common";
  }
}
