/**
 * DESK-021: halt and resume over SSH. Off by default behind a Settings flag (stored in
 * userData). The only write path in NEM Desk: it runs the engine's own `nem halt` /
 * `nem resume` on the VPS configured for sync. Main re-validates every request (flag,
 * scope, reason, typed confirmation, paper-only) and fails closed; nothing is retried.
 *
 * The validators here are pure so the renderer can show the same rules; main never trusts
 * the renderer's checks and runs them again.
 */

import type { Mode } from "./data";

export type HaltAction = "halt" | "resume";

/** `*` (everything), `portfolio` or `portfolio/strategy`. A part never starts with `-`. */
export const SCOPE_RE = /^(?:\*|[A-Za-z0-9_][A-Za-z0-9_-]{0,63}(?:\/[A-Za-z0-9_][A-Za-z0-9_-]{0,63})?)$/;
export const REASON_MAX = 200;
/** Printable ASCII only: no control characters, newlines or non-ASCII look-alikes. */
const REASON_RE = /^[\x20-\x7E]+$/;

export function scopeError(scope: unknown): string | null {
  if (typeof scope !== "string" || !SCOPE_RE.test(scope)) {
    return "Scope: * (everything), a portfolio name, or portfolio/strategy (letters, digits, _ and -).";
  }
  return null;
}

export function reasonError(reason: unknown): string | null {
  if (typeof reason !== "string" || reason.trim() === "") return "A reason is required (nem status shows it).";
  if (reason.length > REASON_MAX) return `Reason: at most ${REASON_MAX} characters.`;
  if (!REASON_RE.test(reason)) return "Reason: printable ASCII only (no newlines or control characters).";
  return null;
}

/** The portfolio a scope names; null for `*`. */
export function scopePortfolio(scope: string): string | null {
  return scope === "*" ? null : (scope.split("/")[0] ?? null);
}

export interface HaltPortfolio {
  name: string;
  /** From the VPS cache's summary.csv; null when unknown (refused: fail closed). */
  mode: Mode | null;
}

/** Why a scope can't be halted or resumed from the app, or null when it can. */
export function liveBlock(scope: string, portfolios: readonly HaltPortfolio[]): string | null {
  const p = scopePortfolio(scope);
  if (p === null) {
    if (portfolios.length === 0) return "No portfolio data from the VPS yet: sync first, so NEM Desk can check every portfolio is paper.";
    const bad = portfolios.filter((x) => x.mode !== "paper");
    if (bad.length > 0) {
      return `Disabled: ${bad.map((x) => `${x.name} (${x.mode ?? "mode unknown"})`).join(", ")} ${bad.length === 1 ? "is" : "are"} not known to be paper. Halt and resume from the app are paper-only until live trading (M8) is reviewed; use the server's terminal.`;
    }
    return null;
  }
  const found = portfolios.find((x) => x.name === p);
  if (found === undefined || found.mode === null) {
    return `Disabled: the mode of portfolio ${p} is unknown (no summary.csv in the VPS cache). Sync first; NEM Desk only acts on portfolios it knows are paper.`;
  }
  if (found.mode !== "paper") {
    return `Disabled: ${p} is a ${found.mode} portfolio. Halt and resume from the app are paper-only until live trading (M8) is reviewed; use the server's terminal.`;
  }
  return null;
}

export interface HaltState {
  enabled: boolean;
  /** `user@host` of the VPS settings, or null when VPS sync isn't configured. */
  target: string | null;
  /** How the command runs on the server, e.g. `cd /home/nem/not-enough-markets && .venv/bin/nem`. */
  runsAs: string | null;
  /** Portfolios and their modes from the VPS cache (last sync). */
  portfolios: HaltPortfolio[];
  /** Strategies (portfolio/strategy) from the VPS cache's status.json. */
  strategies: string[];
  /** Active halts from the VPS cache's status.json (the scopes resume accepts). */
  halts: { scope: string; reason: string; since: string }[];
  running: boolean;
}

export interface HaltRequest {
  action: HaltAction;
  scope: string;
  /** Required for halt; ignored for resume. */
  reason?: string;
  /** Must equal `scope` exactly (typed by the user). */
  confirm: string;
}

export type HaltErrorKind =
  | "disabled"
  | "invalid"
  | "confirm"
  | "live"
  | "not_halted"
  | "not_configured"
  | "busy"
  | "missing_tool"
  | "host_key"
  | "auth"
  | "unreachable"
  | "timeout"
  /** The remote command ran and exited nonzero. */
  | "remote"
  | "failed";

export interface HaltError {
  kind: HaltErrorKind;
  message: string;
  detail?: string;
}

export type HaltResult =
  | { ok: true; command: string; output: string; synced: boolean; state: HaltState }
  | { ok: false; command: string | null; output: string; error: HaltError; state: HaltState };

export interface HaltApi {
  get: () => Promise<HaltState>;
  setEnabled: (enabled: boolean) => Promise<HaltState>;
  run: (request: HaltRequest) => Promise<HaltResult>;
}
