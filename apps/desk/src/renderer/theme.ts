/**
 * NEM Desk design tokens (spec Section 4). This is the ONLY file allowed to contain hex
 * colors; `npm run lint` fails on hex anywhere else. Everything else reads colors through
 * the CSS custom properties written by `applyTheme`, e.g. `var(--amber)`.
 *
 * Keep this module free of DOM access at import time: the main process imports
 * `colors.bg` for the window background.
 */

export interface ColorToken {
  /** CSS custom property name without the leading `--`. */
  name: string;
  value: string;
  use: string;
  /** True when the token is used for text and must reach 4.5:1 on bg and panel. */
  text: boolean;
}

export const colorTokens = [
  { name: "bg", value: "#0A0A0A", use: "window background", text: false },
  { name: "panel", value: "#111315", use: "panes", text: false },
  { name: "border", value: "#2A2E32", use: "borders, dividers", text: false },
  { name: "text", value: "#D7DAD9", use: "primary text", text: true },
  { name: "dim", value: "#7C8489", use: "secondary text", text: true },
  { name: "amber", value: "#FFB000", use: "headings, selection, command bar", text: true },
  { name: "up", value: "#3DDC84", use: "gains, value went up", text: true },
  { name: "down", value: "#FF4D4D", use: "losses, value went down", text: true },
  { name: "tier-common", value: "#9AA0A6", use: "UNDECIDED (edge <= 0)", text: true },
  { name: "tier-rare", value: "#5B8CFF", use: "UNDECIDED+ (edge > 0)", text: true },
  { name: "tier-legendary", value: "#FFC247", use: "EDGE", text: true },
  { name: "tier-cursed", value: "#FF4D4D", use: "NO EDGE", text: true },
] as const satisfies readonly ColorToken[];

export type ColorName = (typeof colorTokens)[number]["name"];

export const colors = Object.fromEntries(colorTokens.map((t) => [t.name, t.value])) as Record<
  ColorName,
  string
>;

/** Translucent variants derived from tokens (selection fill, flash backgrounds). */
export const derivedColors = {
  "amber-soft": hexAlpha(colors.amber, 0.16),
  "up-soft": hexAlpha(colors.up, 0.22),
  "down-soft": hexAlpha(colors.down, 0.22),
  scrim: hexAlpha(colors.bg, 0.72),
} as const;

/**
 * Line colors for charts with several strategies (Compare). Deliberately not the tier
 * colors, which stay reserved for verdicts.
 */
export const chartSeriesColors = ["#FFB000", "#4FD1C5", "#C792EA", "#D7DAD9"] as const;

export const fonts = {
  mono: '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  pixel: '"Silkscreen", "JetBrains Mono", ui-monospace, monospace',
} as const;

export const FONT_SIZE_MIN = 11;
export const FONT_SIZE_MAX = 16;
export const FONT_SIZE_DEFAULT = 13;

/** Type scale as multiples of the user's base font size (11-16px). */
export const typeScale = [
  { name: "xs", factor: 0.85, use: "status bar, hints" },
  { name: "sm", factor: 0.92, use: "table cells, secondary" },
  { name: "md", factor: 1, use: "body" },
  { name: "lg", factor: 1.15, use: "pane titles" },
  { name: "xl", factor: 1.4, use: "screen headings" },
] as const;

export const layout = {
  "row-height": "20px",
  "bar-height": "28px",
  "status-height": "22px",
  "pane-title-height": "26px",
  "nav-width-default": "232px",
  radius: "0px",
  gap: "8px",
  "flash-ms": "150ms",
} as const;

export function hexToRgb(hex: string): [number, number, number] {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (m?.[1] === undefined) throw new Error(`not a 6-digit hex color: ${hex}`);
  const n = Number.parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function hexAlpha(hex: string, alpha: number): string {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** WCAG 2.x relative luminance. */
export function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** Every CSS custom property the app uses, as `[--name, value]` pairs. */
export function cssVariables(fontSizePx: number = FONT_SIZE_DEFAULT): [string, string][] {
  const vars: [string, string][] = [];
  for (const t of colorTokens) vars.push([`--${t.name}`, t.value]);
  for (const [k, v] of Object.entries(derivedColors)) vars.push([`--${k}`, v]);
  vars.push(["--font-mono", fonts.mono], ["--font-pixel", fonts.pixel]);
  vars.push(["--font-size", `${fontSizePx}px`]);
  for (const s of typeScale) vars.push([`--fs-${s.name}`, `calc(var(--font-size) * ${s.factor})`]);
  for (const [k, v] of Object.entries(layout)) vars.push([`--${k}`, v]);
  return vars;
}

/** Minimal structural type so this file needs no DOM lib at import time. */
interface StyleTarget {
  setProperty(name: string, value: string): void;
}

/** Writes the tokens onto an element's inline style (CSSOM, so the strict CSP allows it). */
export function applyTheme(style: StyleTarget, fontSizePx: number = FONT_SIZE_DEFAULT): void {
  for (const [k, v] of cssVariables(fontSizePx)) style.setProperty(k, v);
}
