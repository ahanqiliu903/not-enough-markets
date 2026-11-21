import { spawn as nodeSpawn } from "node:child_process";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import {
  liveBlock,
  reasonError,
  scopeError,
  type HaltAction,
  type HaltError,
  type HaltPortfolio,
  type HaltResult,
  type HaltState,
} from "@shared/halt";
import type { StatusReport } from "@shared/data";
import type { VpsConfig } from "@shared/vps";
import { classify as classifySsh, runTool, sshOptions, validateConfig, type RunResult, type SpawnLike } from "../vps/sync";

/**
 * DESK-021: `nem halt` / `nem resume` on the VPS over the system ssh, behind a flag that
 * is off by default. ssh hands the remote shell one command string, so it is built here
 * from validated values only, each POSIX single-quoted. Every failure (flag off, bad
 * input, confirmation mismatch, non-paper or unknown portfolio, ssh error, unknown host
 * key, nonzero exit, timeout) is reported and nothing is retried.
 *
 * Remote user: the engine's database belongs to the `nem` user (deploy/RUNBOOK.md). When
 * the configured login is `nem`, the command runs directly; otherwise it runs as
 * `sudo -n -u nem` (the RUNBOOK's own pattern, e.g. `sudo -u nem ... nem report`). `-n`
 * means sudo never prompts: a login that needs a password for sudo fails closed.
 */

export const ENGINE_USER = "nem";
export const HALT_TIMEOUT_MS = 60_000;
const OUTPUT_MAX = 8_000;

/** POSIX shell single-quoting: the only special character inside '...' is ' itself. */
export function shQuote(s: string): string {
  return `'${s.replace(/'/g, `'\\''`)}'`;
}

export type CheckedRequest = { action: "halt"; scope: string; reason: string } | { action: "resume"; scope: string };

/** Validates a renderer request (flag, live and halt-exists checks are separate). */
export function checkRequest(raw: unknown): { ok: true; req: CheckedRequest } | { ok: false; error: HaltError } {
  const r = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
  const action = r["action"];
  if (action !== "halt" && action !== "resume") return { ok: false, error: { kind: "invalid", message: "Action: halt or resume." } };
  const scope = r["scope"];
  const se = scopeError(scope);
  if (se !== null || typeof scope !== "string") return { ok: false, error: { kind: "invalid", message: se ?? "Invalid scope." } };
  if (r["confirm"] !== scope) {
    return { ok: false, error: { kind: "confirm", message: `Type the scope exactly (${scope}) to confirm.` } };
  }
  if (action === "resume") return { ok: true, req: { action, scope } };
  const reason = r["reason"];
  const re = reasonError(reason);
  if (re !== null || typeof reason !== "string") return { ok: false, error: { kind: "invalid", message: re ?? "Invalid reason." } };
  return { ok: true, req: { action, scope, reason: reason.trim() } };
}

/** `cd <base> && [sudo -n -u nem] .venv/bin/nem` for display and for the remote command. */
export function remotePrefix(c: VpsConfig): string {
  const sudo = c.user === ENGINE_USER ? "" : `sudo -n -u ${ENGINE_USER} `;
  return `cd ${shQuote(c.remoteBase)} && ${sudo}.venv/bin/nem`;
}

/**
 * The one string ssh sends. The reason goes as a single `--reason=...` word so a reason
 * starting with `-` can't be read as an option; scope parts never start with `-`.
 */
export function remoteCommand(c: VpsConfig, req: CheckedRequest): string {
  const args = req.action === "halt" ? [shQuote(req.scope), shQuote(`--reason=${req.reason}`)] : [shQuote(req.scope)];
  return `${remotePrefix(c)} ${req.action} ${args.join(" ")}`;
}

export function sshArgs(c: VpsConfig, command: string): string[] {
  return [...sshOptions(c), "-T", "--", `${c.user}@${c.host}`, command];
}

function tail(s: string, max = OUTPUT_MAX): string {
  const t = s.trim();
  return t.length > max ? `...${t.slice(-max)}` : t;
}

/** A failed run as a clear error. ssh's own failures (exit 255) reuse the sync classifier. */
export function classifyRun(r: RunResult, c: VpsConfig, action: HaltAction): HaltError {
  if (r.spawnError !== undefined) {
    return r.spawnError === "ENOENT"
      ? { kind: "missing_tool", message: "ssh is not installed or not on PATH." }
      : { kind: "failed", message: `Could not start ssh: ${r.spawnError}` };
  }
  if (r.timedOut === true) {
    return { kind: "timeout", message: `ssh took longer than ${HALT_TIMEOUT_MS / 1000}s and was stopped. The ${action} may or may not have happened: check nem status on the server.` };
  }
  const stderr = r.stderr;
  const detail = tail(`${r.stdout ?? ""}\n${stderr}`, 2_000);
  // ssh exits 255 for its own errors; anything else is the remote command's exit code.
  if (r.code === 255 || r.code === null) {
    const ssh = classifySsh("out", r.code, stderr, c);
    if (ssh.kind === "host_key" || ssh.kind === "auth" || ssh.kind === "unreachable") {
      return { kind: ssh.kind, message: ssh.message, detail };
    }
    return { kind: "failed", message: `ssh to ${c.user}@${c.host} failed (exit ${String(r.code)}).`, detail };
  }
  if (/sudo: (?:a password is required|a terminal is required)|is not in the sudoers|not allowed to execute/i.test(stderr)) {
    return {
      kind: "remote",
      message: `${c.user} can't run commands as ${ENGINE_USER} without a password. Log in as ${ENGINE_USER} (VPS settings: user), or allow passwordless sudo -u ${ENGINE_USER} for ${c.user}.`,
      detail,
    };
  }
  if (r.code === 127 || /\.venv\/bin\/nem: (?:No such file|not found)/i.test(stderr)) {
    return { kind: "remote", message: `${c.remoteBase}/.venv/bin/nem was not found on the server (check the remote folder).`, detail };
  }
  if (/can't cd|cd: .*No such file/i.test(stderr)) {
    return { kind: "remote", message: `The remote folder ${c.remoteBase} does not exist on the server.`, detail };
  }
  return { kind: "remote", message: `nem ${action} exited with code ${String(r.code)} on the server.`, detail };
}

/** Portfolio modes, known strategies and halts from the VPS cache (last sync). */
export interface CacheView {
  portfolios: HaltPortfolio[];
  status: StatusReport | null;
}

interface Stored {
  version: 1;
  enabled: boolean;
}

export interface HaltLog {
  info(area: string, message: string): void;
  warn(area: string, message: string): void;
}

export interface HaltManagerOptions {
  storePath: string;
  /** The saved VPS settings (null when not configured). */
  vpsConfig: () => VpsConfig | null;
  /** Reads the VPS cache; called fresh for every request so a sync is seen at once. */
  cache: () => CacheView;
  /** Refreshes the cache after a successful command; resolves true when the sync worked. */
  sync: () => Promise<boolean>;
  log: () => HaltLog;
  spawn?: SpawnLike;
  timeoutMs?: number;
}

export class HaltManager {
  private enabled = false;
  private running = false;
  private readonly spawn: SpawnLike;

  constructor(private readonly opts: HaltManagerOptions) {
    this.spawn = opts.spawn ?? nodeSpawn;
    try {
      const raw = JSON.parse(readFileSync(opts.storePath, "utf8")) as Partial<Stored>;
      this.enabled = raw.enabled === true;
    } catch {
      // first run: off
    }
  }

  state(): HaltState {
    const c = this.opts.vpsConfig();
    const valid = c === null ? null : validateConfig(c);
    const cfg = valid?.ok === true ? valid.config : null;
    const view = this.opts.cache();
    return {
      enabled: this.enabled,
      target: c === null ? null : `${c.user}@${c.host}`,
      runsAs: cfg === null ? null : remotePrefix(cfg),
      portfolios: view.portfolios,
      strategies: (view.status?.strategies ?? []).map((s) => `${s.portfolio}/${s.strategy}`),
      halts: view.status?.halts ?? [],
      running: this.running,
    };
  }

  setEnabled(on: unknown): HaltState {
    this.enabled = on === true;
    this.opts.log().info("halt", `halt/resume over SSH ${this.enabled ? "enabled" : "disabled"}`);
    try {
      mkdirSync(dirname(this.opts.storePath), { recursive: true });
      const tmp = `${this.opts.storePath}.tmp`;
      writeFileSync(tmp, JSON.stringify({ version: 1, enabled: this.enabled } satisfies Stored, null, 2), { mode: 0o600 });
      renameSync(tmp, this.opts.storePath);
    } catch {
      // Not persisting means it is off again next start: the safe default.
    }
    return this.state();
  }

  async run(raw: unknown): Promise<HaltResult> {
    const fail = (error: HaltError, command: string | null = null, output = ""): HaltResult => {
      this.opts.log().warn("halt", `refused/failed ${describe(raw)}: ${error.kind}: ${error.message}`);
      return { ok: false, command, output, error, state: this.state() };
    };
    if (!this.enabled) return fail({ kind: "disabled", message: "Halt/resume over SSH is off (Settings)." });
    if (this.running) return fail({ kind: "busy", message: "Another halt/resume is still running." });
    const checked = checkRequest(raw);
    if (!checked.ok) return fail(checked.error);
    const req = checked.req;
    const c0 = this.opts.vpsConfig();
    if (c0 === null) return fail({ kind: "not_configured", message: "Set up VPS sync first (Settings)." });
    const v = validateConfig(c0);
    if (!v.ok) return fail({ kind: "not_configured", message: `VPS settings are invalid: ${v.error.message}` });
    const c = v.config;
    const view = this.opts.cache();
    const blocked = liveBlock(req.scope, view.portfolios);
    if (blocked !== null) return fail({ kind: "live", message: blocked });
    if (req.action === "resume" && !(view.status?.halts ?? []).some((h) => h.scope === req.scope)) {
      return fail({ kind: "not_halted", message: `${req.scope} is not halted in the last synced status.json. Sync, then pick an active halt.` });
    }

    const command = remoteCommand(c, req);
    const shown = `ssh ${c.user}@${c.host} ${command}`;
    this.opts.log().info("halt", `attempt ${describe(req)} on the VPS (user ${c.user})`);
    this.running = true;
    let r: RunResult;
    try {
      r = await runTool(this.spawn, "ssh", sshArgs(c, command), this.opts.timeoutMs ?? HALT_TIMEOUT_MS);
    } finally {
      this.running = false;
    }
    const output = tail(`${r.stdout ?? ""}${r.stderr !== "" ? `\n${r.stderr}` : ""}`);
    if (r.spawnError !== undefined || r.timedOut === true || r.code !== 0) {
      return fail(classifyRun(r, c, req.action), shown, output);
    }
    this.opts.log().info("halt", `ok ${describe(req)}`);
    let synced: boolean;
    try {
      synced = await this.opts.sync();
    } catch {
      synced = false;
    }
    return { ok: true, command: shown, output, synced, state: this.state() };
  }
}

/** One line for the log; only validated or length-capped values. */
function describe(raw: unknown): string {
  const r = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
  const clip = (v: unknown) => (typeof v === "string" ? JSON.stringify(v.slice(0, 80)) : "?");
  const reason = r["reason"] !== undefined ? ` reason=${clip(r["reason"])}` : "";
  return `${clip(r["action"])} scope=${clip(r["scope"])}${reason}`;
}
