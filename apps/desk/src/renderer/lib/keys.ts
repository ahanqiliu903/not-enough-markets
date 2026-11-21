import { useEffect, useLayoutEffect, useRef } from "react";

/**
 * Small key-binding layer. Combos are strings like "mod+k", "mod+1", "j", "shift+g",
 * "Enter", "Escape", "/". `mod` means Cmd on macOS and Ctrl elsewhere.
 *
 * Plain keys (no mod/ctrl/alt/meta) are ignored while typing in an input, textarea,
 * select or contenteditable, except "Escape", unless the layer sets `inInputs` (the
 * command palette does, so Enter and arrows work in its search box). Handlers return `false` to let the event
 * continue to other bindings; anything else stops it (preventDefault + stopPropagation).
 */

export type KeyHandler = (event: KeyboardEvent) => boolean | void;
export type KeyBindings = Record<string, KeyHandler>;

export interface ParsedCombo {
  key: string;
  mod: boolean;
  ctrl: boolean;
  alt: boolean;
  shift: boolean;
  meta: boolean;
}

export function isMac(platform: string = detectPlatform()): boolean {
  return platform === "darwin" || /^mac/i.test(platform);
}

function detectPlatform(): string {
  const w = globalThis as { nem?: { platform?: string }; navigator?: { platform?: string } };
  return w.nem?.platform ?? w.navigator?.platform ?? "";
}

export function parseCombo(combo: string): ParsedCombo {
  const parts = combo.split("+");
  // "mod++" style isn't supported; "+" alone is the plus key.
  const key = parts.length > 1 && parts.at(-1) === "" ? "+" : (parts.pop() ?? "");
  const mods = new Set(parts.map((p) => p.toLowerCase()));
  return {
    key: key.length === 1 ? key.toLowerCase() : key,
    mod: mods.has("mod"),
    ctrl: mods.has("ctrl"),
    alt: mods.has("alt"),
    shift: mods.has("shift"),
    meta: mods.has("meta") || mods.has("cmd"),
  };
}

type KeyLike = Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey">;

export function matchCombo(event: KeyLike, combo: string, mac: boolean = isMac()): boolean {
  const c = parseCombo(combo);
  const wantMeta = c.meta || (c.mod && mac);
  const wantCtrl = c.ctrl || (c.mod && !mac);
  if (event.metaKey !== wantMeta || event.ctrlKey !== wantCtrl || event.altKey !== c.alt) {
    return false;
  }
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  // Shift is implied by characters like "/" vs "?"; only enforce it for letters/named keys.
  if (c.shift !== event.shiftKey && /^[a-z]$|^[A-Z][a-zA-Z]+$/.test(c.key)) return false;
  return key === c.key;
}

export function isPlainCombo(combo: string): boolean {
  const c = parseCombo(combo);
  return !c.mod && !c.ctrl && !c.alt && !c.meta;
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (target === null || typeof target !== "object") return false;
  const el = target as { tagName?: string; isContentEditable?: boolean };
  const tag = el.tagName?.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable === true;
}

/** Finds and runs the first binding matching the event. Returns true if one handled it. */
export function dispatchKey(
  event: KeyboardEvent,
  bindings: KeyBindings,
  mac = isMac(),
  inInputs = false,
): boolean {
  const editable = !inInputs && isEditableTarget(event.target);
  for (const [combo, handler] of Object.entries(bindings)) {
    if (!matchCombo(event, combo, mac)) continue;
    if (editable && isPlainCombo(combo) && combo !== "Escape") continue;
    if (handler(event) === false) continue;
    event.preventDefault();
    event.stopPropagation();
    return true;
  }
  return false;
}

/**
 * Key layers. One window listener dispatches to every mounted `useKeys` layer, highest
 * priority first (ties: most recently mounted first). A `modal` layer (e.g. the command
 * palette) swallows every key it doesn't handle so the screen underneath stays still.
 */
export const KEY_PRIORITY = { shell: 0, screen: 10, modal: 100 } as const;

interface Layer {
  ref: { current: KeyBindings };
  priority: number;
  modal: boolean;
  inInputs: boolean;
  order: number;
}

const layers = new Set<Layer>();
let nextOrder = 0;
let listening = false;

function onWindowKey(e: KeyboardEvent): void {
  if (e.defaultPrevented) return;
  const sorted = [...layers].sort((a, b) => b.priority - a.priority || b.order - a.order);
  for (const layer of sorted) {
    if (dispatchKey(e, layer.ref.current, isMac(), layer.inInputs)) return;
    if (layer.modal) return;
  }
}

function addLayer(layer: Layer): () => void {
  layers.add(layer);
  if (!listening) {
    window.addEventListener("keydown", onWindowKey);
    listening = true;
  }
  return () => {
    layers.delete(layer);
    if (layers.size === 0 && listening) {
      window.removeEventListener("keydown", onWindowKey);
      listening = false;
    }
  };
}

export interface UseKeysOptions {
  enabled?: boolean;
  /** Defaults to KEY_PRIORITY.screen. */
  priority?: number;
  modal?: boolean;
  /** Also fire plain keys while focus is in a text field. */
  inInputs?: boolean;
}

/**
 * Binds keys while the component is mounted and `enabled` is true. The bindings object
 * may change every render; the latest one is always used.
 *
 * Screens: `useKeys({ j: down, k: up, Enter: open, Escape: back, c: addToCompare })`.
 * The shell owns mod+1..8, mod+k, "/", "r" and a fallback "Escape" (go back).
 */
export function useKeys(
  bindings: KeyBindings,
  {
    enabled = true,
    priority = KEY_PRIORITY.screen,
    modal = false,
    inInputs = false,
  }: UseKeysOptions = {},
): void {
  const ref = useRef(bindings);
  useLayoutEffect(() => {
    ref.current = bindings;
  });
  useEffect(() => {
    if (!enabled) return;
    return addLayer({ ref, priority, modal, inInputs, order: nextOrder++ });
  }, [enabled, priority, modal, inInputs]);
}

/** Human label for a combo, e.g. "mod+k" -> "Cmd+K" on macOS, "Ctrl+K" elsewhere. */
export function comboLabel(combo: string, mac = isMac()): string {
  const c = parseCombo(combo);
  const parts: string[] = [];
  if (c.mod) parts.push(mac ? "Cmd" : "Ctrl");
  if (c.ctrl) parts.push("Ctrl");
  if (c.alt) parts.push(mac ? "Opt" : "Alt");
  if (c.shift) parts.push("Shift");
  if (c.meta) parts.push("Cmd");
  parts.push(c.key.length === 1 ? c.key.toUpperCase() : c.key);
  return parts.join("+");
}
