import { spawn as nodeSpawn } from "node:child_process";
import { mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join } from "node:path";
import {
  DEFAULT_REMOTE_BASE,
  type VpsConfig,
  type VpsError,
  type VpsSaveResult,
  type VpsState,
  type VpsStep,
  type VpsSyncInfo,
} from "@shared/vps";

/**
 * VPS sync (DESK-013): pulls `data/snapshot/nem.db` and `out/` from the server with the
 * system `rsync` over the system `ssh`. Processes are spawned with argument arrays and no
 * shell. ssh runs with `BatchMode=yes` (never prompts, so no password can be asked for or
 * stored) and `StrictHostKeyChecking=yes` (the host key must already be in the user's
 * known_hosts; an unknown or changed key fails the sync instead of being accepted).
 * Config values are validated to a conservative character set before they reach any
 * argument, and every remote path is fixed relative to the configured base.
 */

export const RSYNC_TIMEOUT_S = 60;
/** Hard cap per rsync run (a stuck transfer is killed). */
export const RUN_TIMEOUT_MS = 10 * 60 * 1000;

export interface ProcLike {
  stdout?: { on(ev: "data", cb: (chunk: unknown) => void): unknown } | null;
  stderr: { on(ev: "data", cb: (chunk: unknown) => void): unknown } | null;
  on(ev: "close", cb: (code: number | null) => void): unknown;
  on(ev: "error", cb: (e: Error & { code?: string }) => void): unknown;
  kill(signal?: NodeJS.Signals): boolean;
}

export type SpawnLike = (
  command: string,
  args: readonly string[],
  options: { shell: false; stdio: ["ignore", "pipe", "pipe"]; env: NodeJS.ProcessEnv },
) => ProcLike;

const HOST = /^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$/;
const USER = /^[A-Za-z_][A-Za-z0-9_.-]{0,31}$/;
// No whitespace, quotes or backslashes: rsync splits its -e string itself.
const KEY_PATH = /^[A-Za-z0-9_./~+@:,=-]+$/;
const REMOTE_BASE = /^\/[A-Za-z0-9_./-]*$/;
export const INTERVALS = [5, 15, 30, 60] as const;

function invalid(field: keyof VpsConfig, message: string): { ok: false; error: VpsError } {
  return { ok: false, error: { kind: "invalid", field, message } };
}

/** Checks a config from the renderer; returns a clean copy or the first problem. */
export function validateConfig(raw: unknown): { ok: true; config: VpsConfig } | { ok: false; error: VpsError } {
  const r = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
  const host = typeof r["host"] === "string" ? r["host"].trim() : "";
  const user = typeof r["user"] === "string" ? r["user"].trim() : "";
  const keyPath = typeof r["keyPath"] === "string" ? r["keyPath"].trim() : "";
  const base = typeof r["remoteBase"] === "string" && r["remoteBase"].trim() !== "" ? r["remoteBase"].trim() : DEFAULT_REMOTE_BASE;
  const port = r["port"] === undefined || r["port"] === null ? 22 : r["port"];
  const interval = r["intervalMinutes"] ?? null;
  if (!HOST.test(host) || host.includes("..")) return invalid("host", "Host: a hostname or IPv4 address (letters, digits, dots, dashes).");
  if (!USER.test(user)) return invalid("user", "User: a Unix user name (letters, digits, _ . -).");
  if (typeof port !== "number" || !Number.isInteger(port) || port < 1 || port > 65535) return invalid("port", "Port: 1 to 65535.");
  if (!isAbsolute(keyPath) || !KEY_PATH.test(keyPath)) {
    return invalid("keyPath", "Key path: an absolute path without spaces or quotes, e.g. /Users/you/.ssh/id_ed25519.");
  }
  try {
    if (!statSync(keyPath).isFile()) return invalid("keyPath", `Key file not found: ${keyPath}`);
  } catch {
    return invalid("keyPath", `Key file not found: ${keyPath}`);
  }
  if (!REMOTE_BASE.test(base) || base.split("/").includes("..")) {
    return invalid("remoteBase", "Remote folder: an absolute path (letters, digits, _ . / -), no '..'.");
  }
  if (interval !== null && !(INTERVALS as readonly unknown[]).includes(interval)) {
    return invalid("intervalMinutes", `Auto sync: off or one of ${INTERVALS.join(", ")} minutes.`);
  }
  return {
    ok: true,
    config: { host, user, port, keyPath, remoteBase: base.replace(/\/+$/, "") || "/", intervalMinutes: interval as number | null },
  };
}

/** The ssh command rsync runs (`-e`). Every value in it passed validateConfig. */
export function sshCommand(c: VpsConfig): string {
  return ["ssh", ...sshOptions(c)].join(" ");
}

/** ssh options shared by sync and DESK-021 halt/resume: never prompt, never accept unknown host keys. */
export function sshOptions(c: VpsConfig): string[] {
  return [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "PasswordAuthentication=no",
    "-o", "KbdInteractiveAuthentication=no",
    "-o", "IdentitiesOnly=yes",
    "-o", "ConnectTimeout=15",
    "-i", c.keyPath,
    "-p", String(c.port),
  ];
}

/** rsync argument arrays for the two pulls. `--` ends options before any path. */
export function rsyncArgs(c: VpsConfig, cacheDir: string): { what: VpsStep["what"]; args: string[] }[] {
  const remote = (p: string) => `${c.user}@${c.host}:${c.remoteBase === "/" ? "" : c.remoteBase}/${p}`;
  const common = ["-az", `--timeout=${RSYNC_TIMEOUT_S}`, "-e", sshCommand(c)];
  return [
    { what: "out", args: [...common, "--delete", "--", remote("out/"), `${join(cacheDir, "out")}/`] },
    { what: "db", args: [...common, "--", remote("data/snapshot/nem.db"), join(cacheDir, "nem.db")] },
  ];
}

function tail(s: string, lines = 6): string {
  return s.trim().split("\n").slice(-lines).join("\n");
}

/** Turns a failed run into a clear error. Host key problems are checked first: fail closed. */
export function classify(what: VpsStep["what"], code: number | null, stderr: string, c: VpsConfig): VpsError {
  const detail = tail(stderr);
  const where = `${c.user}@${c.host}`;
  if (/REMOTE HOST IDENTIFICATION HAS CHANGED|Host key verification failed|host key is known|No \S+ host key is known/i.test(stderr)) {
    return {
      kind: "host_key",
      message: `The host key for ${c.host} is not in your known_hosts, or it changed. NEM Desk never accepts unknown keys. Verify the server's fingerprint, then connect once in a terminal (ssh -p ${c.port} ${where}) to record it, and sync again.`,
      detail,
    };
  }
  if (/Permission denied|Too many authentication failures|no mutual signature/i.test(stderr)) {
    return { kind: "auth", message: `${where} refused the key ${c.keyPath}. Check the user and that the key is in the server's authorized_keys (and loaded if it has a passphrase: ssh-add).`, detail };
  }
  if (/Could not resolve hostname|Connection refused|timed out|No route to host|Network is unreachable|Connection closed|Connection reset/i.test(stderr)) {
    return { kind: "unreachable", message: `Could not reach ${c.host}:${c.port}.`, detail };
  }
  if (code === 23 || /No such file or directory|change_dir .* failed|link_stat/i.test(stderr)) {
    const missing = what === "db" ? `${c.remoteBase}/data/snapshot/nem.db` : `${c.remoteBase}/out/`;
    const hint = what === "db" ? " Enable the snapshot timer (deploy/RUNBOOK.md, \"Snapshots for NEM Desk\")." : " Check the remote folder, and that nem report writes out/.";
    return { kind: "remote_missing", message: `${missing} does not exist on the server.${hint}`, detail };
  }
  if (code === 30 || code === 35) return { kind: "timeout", message: `rsync timed out talking to ${c.host}.`, detail };
  return { kind: "failed", message: `rsync of ${what === "db" ? "the snapshot database" : "out/"} failed (exit ${String(code)}).`, detail };
}

export interface RunResult {
  code: number | null;
  stderr: string;
  /** Captured (capped) only for DESK-021, which shows the command's output. */
  stdout?: string;
  spawnError?: string;
  timedOut?: boolean;
}

export function runTool(spawn: SpawnLike, cmd: string, args: readonly string[], timeoutMs = RUN_TIMEOUT_MS): Promise<RunResult> {
  return new Promise((done) => {
    let stderr = "";
    let stdout = "";
    let finished = false;
    const finish = (r: RunResult) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      done(r);
    };
    let proc: ProcLike;
    try {
      proc = spawn(cmd, args, {
        shell: false,
        stdio: ["ignore", "pipe", "pipe"],
        // C locale so the messages classify() matches are in English.
        env: { ...process.env, LC_ALL: "C", LANG: "C" },
      });
    } catch (e) {
      done({ code: null, stderr: "", spawnError: e instanceof Error ? e.message : String(e) });
      return;
    }
    const timer = setTimeout(() => {
      proc.kill("SIGTERM");
      finish({ code: null, stderr, stdout, timedOut: true });
    }, timeoutMs);
    proc.stderr?.on("data", (d) => {
      if (stderr.length < 64_000) stderr += String(d);
    });
    proc.stdout?.on("data", (d) => {
      if (stdout.length < 64_000) stdout += String(d);
    });
    proc.on("error", (e) => finish({ code: null, stderr, stdout, spawnError: e.code === "ENOENT" ? "ENOENT" : e.message }));
    proc.on("close", (code) => finish({ code, stderr, stdout }));
  });
}

interface Stored {
  version: 1;
  config: VpsConfig | null;
  lastSync: VpsSyncInfo | null;
}

export interface VpsManagerOptions {
  storePath: string;
  cacheDir: string;
  spawn?: SpawnLike;
  /** Called after a sync that brought anything down: add or refresh the cache as a source. */
  onSynced?: (cacheDir: string, config: VpsConfig) => void;
  now?: () => Date;
  /** Turn off the auto-sync timer (tests). */
  timers?: boolean;
}

export class VpsManager {
  private config: VpsConfig | null = null;
  private lastSync: VpsSyncInfo | null = null;
  private running: Promise<VpsSyncInfo> | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private readonly spawn: SpawnLike;

  constructor(private readonly opts: VpsManagerOptions) {
    this.spawn = opts.spawn ?? nodeSpawn;
    try {
      const raw = JSON.parse(readFileSync(opts.storePath, "utf8")) as Partial<Stored>;
      if (raw.config !== null && raw.config !== undefined) {
        // Re-validate on load (key file may be gone); keep the stored values for editing.
        const v = validateConfig(raw.config);
        this.config = v.ok ? v.config : raw.config;
      }
      this.lastSync = raw.lastSync ?? null;
    } catch {
      // first run
    }
    this.retime();
  }

  state(): VpsState {
    return { config: this.config, cacheDir: this.opts.cacheDir, syncing: this.running !== null, lastSync: this.lastSync };
  }

  private write(): void {
    const data: Stored = { version: 1, config: this.config, lastSync: this.lastSync };
    try {
      mkdirSync(dirname(this.opts.storePath), { recursive: true });
      const tmp = `${this.opts.storePath}.tmp`;
      writeFileSync(tmp, JSON.stringify(data, null, 2), { mode: 0o600 });
      renameSync(tmp, this.opts.storePath);
    } catch {
      // not persisting is survivable
    }
  }

  save(raw: unknown): VpsSaveResult {
    const v = validateConfig(raw);
    if (!v.ok) return { ok: false, state: this.state(), error: v.error };
    this.config = v.config;
    this.write();
    this.retime();
    return { ok: true, state: this.state() };
  }

  forget(): VpsState {
    this.config = null;
    this.lastSync = null;
    this.write();
    this.retime();
    return this.state();
  }

  private retime(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    const m = this.config?.intervalMinutes ?? null;
    if (m === null || this.opts.timers === false) return;
    this.timer = setInterval(() => void this.sync(), m * 60_000);
    this.timer.unref();
  }

  /** One pull; concurrent calls share the run in progress. */
  sync(): Promise<VpsSyncInfo> {
    this.running ??= this.doSync().finally(() => {
      this.running = null;
    });
    return this.running;
  }

  private async doSync(): Promise<VpsSyncInfo> {
    const at = (this.opts.now?.() ?? new Date()).toISOString();
    const finish = (info: VpsSyncInfo): VpsSyncInfo => {
      this.lastSync = info;
      this.write();
      return info;
    };
    if (this.config === null) {
      return { at, ok: false, steps: [], error: { kind: "not_configured", message: "Set host, user and key path first." } };
    }
    const v = validateConfig(this.config);
    if (!v.ok) return finish({ at, ok: false, steps: [], error: v.error });
    const c = v.config;
    try {
      mkdirSync(join(this.opts.cacheDir, "out"), { recursive: true });
    } catch (e) {
      return finish({ at, ok: false, steps: [], error: { kind: "failed", message: `Cannot create the cache folder: ${String(e)}` } });
    }
    const steps: VpsStep[] = [];
    for (const { what, args } of rsyncArgs(c, this.opts.cacheDir)) {
      const r = await runTool(this.spawn, "rsync", args);
      if (r.spawnError !== undefined) {
        const error: VpsError =
          r.spawnError === "ENOENT"
            ? { kind: "missing_tool", message: "rsync is not installed (it ships with macOS; on Windows install it with ssh, e.g. via WSL or cwRsync)." }
            : { kind: "failed", message: `Could not start rsync: ${r.spawnError}` };
        steps.push({ what, ok: false, error });
        return finish({ at, ok: false, steps, error });
      }
      if (r.timedOut === true) {
        const error: VpsError = { kind: "timeout", message: `rsync took longer than ${RUN_TIMEOUT_MS / 60000} minutes and was stopped.` };
        steps.push({ what, ok: false, error });
        return finish({ at, ok: false, steps, error });
      }
      if (r.code === 0) {
        steps.push({ what, ok: true });
        continue;
      }
      const error = classify(what, r.code, r.stderr, c);
      steps.push({ what, ok: false, error });
      // Connection problems will fail the next pull the same way: stop, fail closed.
      if (error.kind === "host_key" || error.kind === "auth" || error.kind === "unreachable") {
        return finish({ at, ok: false, steps, error });
      }
    }
    const ok = steps.every((s) => s.ok);
    if (steps.some((s) => s.ok)) this.opts.onSynced?.(this.opts.cacheDir, c);
    const firstError = steps.find((s) => !s.ok)?.error;
    return finish({ at, ok, steps, ...(firstError !== undefined ? { error: firstError } : {}) });
  }

  dispose(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
  }
}
