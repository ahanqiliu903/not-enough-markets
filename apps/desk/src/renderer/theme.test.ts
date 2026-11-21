import { describe, expect, it } from "vitest";
import { colors, colorTokens, contrastRatio, cssVariables, luminance } from "./theme";

describe("contrast", () => {
  it("computes WCAG ratios correctly", () => {
    expect(contrastRatio("#000000", "#FFFFFF")).toBeCloseTo(21, 5);
    expect(contrastRatio("#777777", "#777777")).toBeCloseTo(1, 5);
    expect(luminance("#FFFFFF")).toBeCloseTo(1, 5);
  });

  const textTokens = colorTokens.filter((t) => t.text);

  it("has text tokens to check", () => {
    expect(textTokens.map((t) => t.name)).toContain("text");
    expect(textTokens.map((t) => t.name)).toContain("dim");
  });

  for (const t of textTokens) {
    for (const bg of ["bg", "panel"] as const) {
      it(`--${t.name} on --${bg} is >= 4.5:1`, () => {
        expect(contrastRatio(t.value, colors[bg])).toBeGreaterThanOrEqual(4.5);
      });
    }
  }
});

describe("cssVariables", () => {
  it("writes every color token and the font size", () => {
    const vars = new Map(cssVariables(15));
    for (const t of colorTokens) expect(vars.get(`--${t.name}`)).toBe(t.value);
    expect(vars.get("--font-size")).toBe("15px");
  });

  it("matches the spec values", () => {
    expect(colors).toMatchObject({
      bg: "#0A0A0A",
      panel: "#111315",
      border: "#2A2E32",
      text: "#D7DAD9",
      dim: "#7C8489",
      amber: "#FFB000",
      up: "#3DDC84",
      down: "#FF4D4D",
      "tier-common": "#9AA0A6",
      "tier-rare": "#5B8CFF",
      "tier-legendary": "#FFC247",
      "tier-cursed": "#FF4D4D",
    });
  });
});
