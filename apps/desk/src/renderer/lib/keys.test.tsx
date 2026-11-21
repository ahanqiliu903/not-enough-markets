import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { comboLabel, KEY_PRIORITY, matchCombo, parseCombo, useKeys, type KeyBindings } from "./keys";

const ev = (key: string, mods: Partial<Record<"metaKey" | "ctrlKey" | "altKey" | "shiftKey", boolean>> = {}) => ({
  key,
  metaKey: false,
  ctrlKey: false,
  altKey: false,
  shiftKey: false,
  ...mods,
});

describe("matchCombo", () => {
  it("treats mod as Cmd on mac and Ctrl elsewhere", () => {
    expect(matchCombo(ev("k", { metaKey: true }), "mod+k", true)).toBe(true);
    expect(matchCombo(ev("k", { ctrlKey: true }), "mod+k", true)).toBe(false);
    expect(matchCombo(ev("k", { ctrlKey: true }), "mod+k", false)).toBe(true);
    expect(matchCombo(ev("k", { metaKey: true }), "mod+k", false)).toBe(false);
  });

  it("requires exact modifiers", () => {
    expect(matchCombo(ev("j"), "j", true)).toBe(true);
    expect(matchCombo(ev("j", { metaKey: true }), "j", true)).toBe(false);
    expect(matchCombo(ev("2", { metaKey: true }), "mod+2", true)).toBe(true);
    expect(matchCombo(ev("J", { shiftKey: true }), "j", true)).toBe(false);
    expect(matchCombo(ev("G", { shiftKey: true }), "shift+g", true)).toBe(true);
  });

  it("matches named keys and punctuation", () => {
    expect(matchCombo(ev("Enter"), "Enter", true)).toBe(true);
    expect(matchCombo(ev("Escape"), "Escape", true)).toBe(true);
    expect(matchCombo(ev("/"), "/", true)).toBe(true);
  });

  it("parses and labels combos", () => {
    expect(parseCombo("mod+shift+P")).toMatchObject({ key: "p", mod: true, shift: true });
    expect(comboLabel("mod+k", true)).toBe("Cmd+K");
    expect(comboLabel("mod+1", false)).toBe("Ctrl+1");
  });
});

function Keys({
  bindings,
  priority,
  modal,
  inInputs,
}: {
  bindings: KeyBindings;
  priority?: number;
  modal?: boolean;
  inInputs?: boolean;
}) {
  useKeys(bindings, {
    ...(priority !== undefined ? { priority } : {}),
    ...(modal !== undefined ? { modal } : {}),
    ...(inInputs !== undefined ? { inInputs } : {}),
  });
  return <input aria-label="field" />;
}

describe("useKeys", () => {
  it("runs j/k/Enter/c handlers and prevents default", () => {
    const j = vi.fn();
    const k = vi.fn();
    const enter = vi.fn();
    const c = vi.fn();
    render(<Keys bindings={{ j, k, Enter: enter, c }} />);
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "k" });
    fireEvent.keyDown(window, { key: "Enter" });
    const notPrevented = fireEvent.keyDown(window, { key: "c" });
    expect([j, k, enter, c].map((f) => f.mock.calls.length)).toEqual([1, 1, 1, 1]);
    expect(notPrevented).toBe(false);
  });

  it("ignores plain keys while typing but still sees Escape and mod combos", () => {
    const j = vi.fn();
    const esc = vi.fn();
    const palette = vi.fn();
    const { getByLabelText } = render(<Keys bindings={{ j, Escape: esc, "mod+k": palette }} />);
    const input = getByLabelText("field");
    fireEvent.keyDown(input, { key: "j" });
    fireEvent.keyDown(input, { key: "Escape" });
    fireEvent.keyDown(input, { key: "k", metaKey: true });
    fireEvent.keyDown(input, { key: "k", ctrlKey: true });
    expect(j).not.toHaveBeenCalled();
    expect(esc).toHaveBeenCalledTimes(1);
    expect(palette).toHaveBeenCalledTimes(1);
  });

  it("fires plain keys in inputs only for inInputs layers", () => {
    const enter = vi.fn();
    const { getByLabelText } = render(<Keys bindings={{ Enter: enter }} inInputs />);
    fireEvent.keyDown(getByLabelText("field"), { key: "Enter" });
    expect(enter).toHaveBeenCalledTimes(1);
  });

  it("lets higher-priority layers win and modal layers block lower ones", () => {
    const shellEsc = vi.fn();
    const screenJ = vi.fn();
    const modalEsc = vi.fn();
    const { rerender } = render(
      <>
        <Keys bindings={{ Escape: shellEsc }} priority={KEY_PRIORITY.shell} />
        <Keys bindings={{ j: screenJ }} priority={KEY_PRIORITY.screen} />
      </>,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(shellEsc).toHaveBeenCalledTimes(1);

    rerender(
      <>
        <Keys bindings={{ Escape: shellEsc }} priority={KEY_PRIORITY.shell} />
        <Keys bindings={{ j: screenJ }} priority={KEY_PRIORITY.screen} />
        <Keys bindings={{ Escape: modalEsc }} priority={KEY_PRIORITY.modal} modal />
      </>,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.keyDown(window, { key: "j" });
    expect(modalEsc).toHaveBeenCalledTimes(1);
    expect(shellEsc).toHaveBeenCalledTimes(1);
    expect(screenJ).not.toHaveBeenCalled();
  });

  it("falls through when a handler returns false", () => {
    const first = vi.fn(() => false);
    const second = vi.fn();
    render(
      <>
        <Keys bindings={{ r: second }} priority={KEY_PRIORITY.shell} />
        <Keys bindings={{ r: first }} priority={KEY_PRIORITY.screen} />
      </>,
    );
    fireEvent.keyDown(window, { key: "r" });
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });
});
