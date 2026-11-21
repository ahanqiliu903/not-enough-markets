import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App, makeQueryClient } from "../App";
import { routes } from "../routes";
import { useSettings } from "../state/settings";
import { fonts } from "../theme";
import { PIXEL_ICON_BITMAPS, PIXEL_ICON_NAMES } from "./PixelIcon";
import { EmptyState, SchemaError, StaleBanner } from "./states";
import { TierBadge } from "./TierBadge";

/** DESK-022: one consistent set of original 16x16 icons, and accents off removes them all. */
describe("pixel icon set", () => {
  it("every icon is a 16x16 bitmap of '#' and '.'", () => {
    for (const name of PIXEL_ICON_NAMES) {
      const rows = PIXEL_ICON_BITMAPS[name];
      expect(rows, name).toHaveLength(16);
      for (const row of rows) expect(row, name).toMatch(/^[#.]{16}$/);
      expect(rows.join("").includes("#"), name).toBe(true);
    }
  });

  it("no two icons are the same picture", () => {
    const seen = new Map<string, string>();
    for (const name of PIXEL_ICON_NAMES) {
      const key = PIXEL_ICON_BITMAPS[name].join("");
      expect(seen.get(key), `${name} duplicates ${seen.get(key) ?? ""}`).toBeUndefined();
      seen.set(key, name);
    }
  });

  it("every screen has its own icon", () => {
    const icons = routes.map((r) => r.icon);
    for (const icon of icons) expect(PIXEL_ICON_NAMES).toContain(icon);
    expect(new Set(icons).size).toBe(icons.length);
  });
});

describe("pixel accents on components", () => {
  it("tier badge has the tier icon and a pixel-font label", () => {
    const { container } = render(<TierBadge verdict="edge" edgeCents={2} />);
    expect(container.querySelector(".tier-badge svg.pixel-icon")).not.toBeNull();
    expect(screen.getByText("EDGE")).toHaveClass("pixel-font");
  });

  it("states carry an icon without changing their accessible names", () => {
    const { container } = render(
      <>
        <EmptyState title="No portfolios yet" reason="none" />
        <SchemaError file="a.csv" field="x" expectedVersion="1" />
        <StaleBanner newest={new Date(0)} now={new Date(3_600_000)} />
      </>,
    );
    expect(screen.getByRole("heading", { name: "No portfolios yet" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Schema error" })).toBeInTheDocument();
    expect(container.querySelectorAll("svg.pixel-icon")).toHaveLength(3);
  });

  it("accents off points the pixel font token at the mono font", () => {
    render(<App client={makeQueryClient()} />);
    const style = document.documentElement.style;
    expect(style.getPropertyValue("--font-pixel")).toBe(fonts.pixel);
    act(() => useSettings.getState().setPixelAccents(false));
    expect(style.getPropertyValue("--font-pixel")).toBe(fonts.mono);
    expect(style.getPropertyValue("--font-pixel")).not.toContain("Silkscreen");
    act(() => useSettings.getState().setPixelAccents(true));
    expect(style.getPropertyValue("--font-pixel")).toBe(fonts.pixel);
  });
});
