import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App, makeQueryClient } from "../App";
import { useNav } from "../state/nav";
import { useSettings } from "../state/settings";
import { LoreCard } from "./LoreCard";
import { SchemaError } from "./states";
import { TierBadge } from "./TierBadge";

describe("TierBadge", () => {
  it("always shows the text label", () => {
    render(<TierBadge verdict="undecided" edgeCents={1.2} />);
    expect(screen.getByText("UNDECIDED+")).toBeInTheDocument();
  });
});

describe("LoreCard", () => {
  it("shows stat lines on focus", () => {
    render(
      <LoreCard title="BTC fav" tier="legendary" stats={[{ label: "win rate", value: "92.0%" }]}>
        badge
      </LoreCard>,
    );
    fireEvent.focus(screen.getByText("badge"));
    expect(screen.getByRole("tooltip")).toHaveTextContent("win rate92.0%");
  });
});

describe("SchemaError", () => {
  it("names file, field and expected version", () => {
    render(<SchemaError file="summary.json" field="schema_version" expectedVersion="1" />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("summary.json");
    expect(alert).toHaveTextContent("schema_version");
    expect(alert).toHaveTextContent("schema version 1");
  });
});

describe("Shell", () => {
  it("switches screens with mod+2 and opens the palette with mod+k", async () => {
    render(<App client={makeQueryClient()} />);
    expect(await screen.findByRole("heading", { name: "Portfolios" })).toBeInTheDocument();
    // jsdom reports a non-mac platform, so mod = Ctrl.
    fireEvent.keyDown(window, { key: "2", ctrlKey: true });
    expect(await screen.findByRole("heading", { name: "Strategy" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(useNav.getState().paletteOpen).toBe(true);
    expect(screen.getByRole("dialog", { name: "Command palette" })).toBeInTheDocument();
    act(() => useNav.getState().setPaletteOpen(false));
  });

  it("pixel accents off flips the root attribute", () => {
    render(<App client={makeQueryClient()} />);
    act(() => useSettings.getState().setPixelAccents(false));
    expect(document.documentElement.dataset["pixel"]).toBe("off");
    act(() => useSettings.getState().setPixelAccents(true));
  });
});
