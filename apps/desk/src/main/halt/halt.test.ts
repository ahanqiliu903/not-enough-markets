// @vitest-environment node
import { EventEmitter } from "node:events";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { liveBlock, scopeError, type HaltPortfolio } from "@shared/halt";
import type { StatusReport } from "@shared/data";
import { DEFAULT_REMOTE_BASE, type VpsConfig } from "@shared/vps";
import type { ProcLike, SpawnLike } from "../vps/sync";
import { checkRequest, classifyRun, HaltManager, remoteCommand, shQuote, sshArgs, type CacheView } from "./manager";

/** No test contacts a server: `spawn` is always this fake. */
interface Call {
  cmd: string;
  args: readonly string[];
  shell: boolean;
}
type Script = { code: number | null; stdout?: string; stderr?: string; error?: string; hang?: boolean };

function fakeSpawn(script: () => Script): { spawn: SpawnLike; calls: Call[] } {
  const calls: Call[] = [];
  const spawn: SpawnLike = (cmd, args, options) => {
    calls.push({ cmd, args, shell: options.shell });
    const r = script();
    const proc = new EventEmitter() as EventEmitter & ProcLike;
    const stderr = new EventEmitter();
    const stdout = new EventEmitter();
    Object.assign(proc, { stderr, stdout, kill: () => true });
    if (r.hang === true) return proc;
    setTimeout(() => {
      if (r.error !== undefined) {
        proc.emit("error", Object.assign(new Error(r.error), { code: r.error }));
        return;
      }
      if (r.stdout !== undefined) stdout.emit("data", Buffer.from(r.stdout));
      if (r.stderr !== undefined) stderr.emit("data", Buffer.from(r.stderr));
      proc.emit("close", r.code);
    }, 0);
    return proc;
  };
  return { spawn, calls };
}

const tmps: string[] = [];
function tmp(): string {
  const d = mkdtempSync(join(tmpdir(), "nem-desk-halt-"));
  tmps.push(d);
  return d;
}
afterEach(() => {
  for (const d of tmps.splice(0)) rmSync(d, { recursive: true, force: true });
});

function config(dir: string, over: Partial<VpsConfig> = {}): VpsConfig {
  const keyPath = join(dir, "id_ed25519");
  writeFileSync(keyPath, "not a real key");
  return { host: "nem.example.org", user: "nem", port: 2222, keyPath, remoteBase: DEFAULT_REMOTE_BASE, intervalMinutes: null, ...over };
}

const STATUS: StatusReport = {
  schema_version: "1.0",
  as_of: "2026-10-02T18:35:00Z",
  healthy: true,
  processes: [],
  halts: [{ scope: "paper0/fav_90_btc", reason: "check fills", since: "2026-10-02T18:30:00Z" }],
  strategies: [],
};
const PAPER: CacheView = { portfolios: [{ name: "paper0", mode: "paper" }], status: STATUS };

function setup(o: { cache?: CacheView; script?: () => Script; enabled?: boolean; user?: string; timeoutMs?: number; sync?: boolean } = {}) {
  const dir = tmp();
  const storePath = join(dir, "halt.json");
  if (o.enabled !== false) writeFileSync(storePath, JSON.stringify({ version: 1, enabled: true }));
  const cfg = config(dir, o.user !== undefined ? { user: o.user } : {});
  const { spawn, calls } = fakeSpawn(o.script ?? (() => ({ code: 0, stdout: "Halted paper0: no new entries." })));
  const lines: string[] = [];
  const logger = { info: (a: string, m: string) => lines.push(`info ${a} ${m}`), warn: (a: string, m: string) => lines.push(`warn ${a} ${m}`) };
  let syncs = 0;
  const m = new HaltManager({
    storePath,
    vpsConfig: () => cfg,
    cache: () => o.cache ?? PAPER,
    sync: () => {
      syncs++;
      return Promise.resolve(o.sync ?? true);
    },
    log: () => logger,
    spawn,
    ...(o.timeoutMs !== undefined ? { timeoutMs: o.timeoutMs } : {}),
  });
  return { m, calls, lines, cfg, storePath, syncs: () => syncs };
}

const halt = (scope: string, reason = "check fills", confirm = scope) => ({ action: "halt", scope, reason, confirm });

describe("validation and escaping", () => {
  it("accepts *, portfolio and portfolio/strategy only", () => {
    for (const ok of ["*", "paper0", "paper0/fav_90_btc", "a-b/c_d"]) expect(scopeError(ok)).toBeNull();
    for (const bad of ["", "**", "a/b/c", "-rf", "a/-x", "a b", "a;rm -rf ~", "$(id)", "`id`", "a'b", "a\nb", "../x", "a/", "/a", 7, null]) {
      expect(scopeError(bad)).not.toBeNull();
    }
  });

  it("rejects injection and junk in the reason", () => {
    const base = { action: "halt", scope: "paper0", confirm: "paper0" };
    expect(checkRequest({ ...base, reason: "" }).ok).toBe(false);
    expect(checkRequest({ ...base, reason: "   " }).ok).toBe(false);
    expect(checkRequest({ ...base, reason: "x".repeat(201) }).ok).toBe(false);
    expect(checkRequest({ ...base, reason: "line\nrm -rf /" }).ok).toBe(false);
    expect(checkRequest({ ...base, reason: "bell\x07" }).ok).toBe(false);
    expect(checkRequest({ ...base, reason: "café" }).ok).toBe(false);
    expect(checkRequest({ ...base, reason: 42 }).ok).toBe(false);
    expect(checkRequest({ action: "rm", scope: "paper0", confirm: "paper0" }).ok).toBe(false);
  });

  it("single-quotes every value so shell metacharacters stay literal", () => {
    expect(shQuote("it's")).toBe(`'it'\\''s'`);
    const c = config(tmp());
    const cmd = remoteCommand(c, { action: "halt", scope: "*", reason: `'; rm -rf ~; echo '$(id) \`id\` "x" -- --db=/tmp/x` });
    expect(cmd).toBe(
      `cd '/home/nem/not-enough-markets' && .venv/bin/nem halt '*' '--reason='\\''; rm -rf ~; echo '\\''$(id) \`id\` "x" -- --db=/tmp/x'`,
    );
    // A reason starting with "-" stays the value of --reason, not a new option.
    expect(remoteCommand(c, { action: "halt", scope: "p", reason: "-x" })).toContain(`'--reason=-x'`);
    expect(remoteCommand(c, { action: "resume", scope: "paper0/fav_90_btc" })).toBe(
      `cd '/home/nem/not-enough-markets' && .venv/bin/nem resume 'paper0/fav_90_btc'`,
    );
  });

  it("runs as nem via sudo -n when the login user is someone else", () => {
    const c = config(tmp(), { user: "root" });
    expect(remoteCommand(c, { action: "resume", scope: "p" })).toBe(`cd '/home/nem/not-enough-markets' && sudo -n -u nem .venv/bin/nem resume 'p'`);
  });

  it("ssh never prompts, checks host keys, and ends options before the destination", () => {
    const c = config(tmp());
    const args = sshArgs(c, "CMD");
    for (const o of ["BatchMode=yes", "StrictHostKeyChecking=yes", "PasswordAuthentication=no"]) expect(args).toContain(o);
    expect(args.slice(-3)).toEqual(["--", "nem@nem.example.org", "CMD"]);
    expect(args).toContain("2222");
  });
});

describe("paper-only", () => {
  const ps = (...x: [string, HaltPortfolio["mode"]][]): HaltPortfolio[] => x.map(([name, mode]) => ({ name, mode }));
  it("refuses live and unknown portfolios, and * unless every portfolio is paper", () => {
    expect(liveBlock("paper0", ps(["paper0", "paper"]))).toBeNull();
    expect(liveBlock("paper0/s", ps(["paper0", "paper"]))).toBeNull();
    expect(liveBlock("real", ps(["real", "live"]))).toMatch(/live portfolio/);
    expect(liveBlock("ghost", ps(["paper0", "paper"]))).toMatch(/unknown/);
    expect(liveBlock("nosum/s", ps(["nosum", null]))).toMatch(/unknown/);
    expect(liveBlock("*", ps(["paper0", "paper"]))).toBeNull();
    expect(liveBlock("*", ps(["paper0", "paper"], ["real", "live"]))).toMatch(/real \(live\)/);
    expect(liveBlock("*", ps(["paper0", "paper"], ["x", null]))).toMatch(/mode unknown/);
    expect(liveBlock("*", [])).toMatch(/sync first/);
  });
});

describe("HaltManager", () => {
  it("is off by default; while off the handler refuses and spawns nothing", async () => {
    const { m, calls, storePath } = setup({ enabled: false });
    expect(m.state().enabled).toBe(false);
    const r = await m.run(halt("paper0"));
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.kind).toBe("disabled");
    expect(calls).toHaveLength(0);
    m.setEnabled(true);
    expect(JSON.parse(readFileSync(storePath, "utf8"))).toEqual({ version: 1, enabled: true });
    expect(m.setEnabled("yes").enabled).toBe(false);
  });

  it("refuses a confirmation that doesn't match the scope exactly", async () => {
    const { m, calls } = setup();
    for (const confirm of ["", "paper0 ", "PAPER0", "paper", "*"]) {
      const r = await m.run(halt("paper0", "x", confirm));
      expect(r.ok ? "ok" : r.error.kind).toBe("confirm");
    }
    expect(calls).toHaveLength(0);
  });

  it("refuses live, unknown-mode and * with a non-paper portfolio without spawning", async () => {
    const cache: CacheView = { portfolios: [{ name: "paper0", mode: "paper" }, { name: "real", mode: "live" }, { name: "x", mode: null }], status: STATUS };
    const { m, calls } = setup({ cache });
    for (const scope of ["real", "real/s", "x", "*", "ghost"]) {
      const r = await m.run(halt(scope));
      expect(r.ok ? "ok" : r.error.kind).toBe("live");
    }
    const resume = await m.run({ action: "resume", scope: "real", confirm: "real" });
    expect(resume.ok ? "ok" : resume.error.kind).toBe("live");
    expect(calls).toHaveLength(0);
  });

  it("resume only accepts a scope halted in the last synced status.json", async () => {
    const { m, calls } = setup();
    const r = await m.run({ action: "resume", scope: "paper0", confirm: "paper0" });
    expect(r.ok ? "ok" : r.error.kind).toBe("not_halted");
    expect(calls).toHaveLength(0);
    const ok = await m.run({ action: "resume", scope: "paper0/fav_90_btc", confirm: "paper0/fav_90_btc" });
    expect(ok.ok).toBe(true);
    expect(calls[0]?.args.at(-1)).toBe(`cd '/home/nem/not-enough-markets' && .venv/bin/nem resume 'paper0/fav_90_btc'`);
  });

  it("runs ssh without a shell, shows the output, syncs once and logs the attempt", async () => {
    const { m, calls, lines, syncs } = setup();
    const r = await m.run(halt("paper0", "  rebalancing  "));
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.output).toBe("Halted paper0: no new entries.");
    expect(r.synced).toBe(true);
    expect(r.command).toContain(`.venv/bin/nem halt 'paper0' '--reason=rebalancing'`);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.cmd).toBe("ssh");
    expect(calls[0]?.shell).toBe(false);
    expect(syncs()).toBe(1);
    expect(lines.some((l) => l.startsWith("info halt attempt") && l.includes("paper0"))).toBe(true);
    expect(lines.some((l) => l.startsWith("info halt ok"))).toBe(true);
    expect(lines.join("\n")).not.toContain("nem.example.org");
  });

  it("fails closed on ssh errors, nonzero exits and timeouts, without retrying or syncing", async () => {
    const cases: [Script, string][] = [
      [{ code: 255, stderr: "Host key verification failed." }, "host_key"],
      [{ code: 255, stderr: "nem@nem.example.org: Permission denied (publickey)." }, "auth"],
      [{ code: 255, stderr: "ssh: Could not resolve hostname nem.example.org" }, "unreachable"],
      [{ code: 255, stderr: "something odd" }, "failed"],
      [{ code: 1, stdout: "No halt on 'paper0'. Active halts: none" }, "remote"],
      [{ code: 1, stderr: "sudo: a password is required" }, "remote"],
      [{ code: 127, stderr: "bash: .venv/bin/nem: No such file or directory" }, "remote"],
      [{ code: null, error: "ENOENT" }, "missing_tool"],
      [{ code: null, hang: true }, "timeout"],
    ];
    for (const [script, kind] of cases) {
      const { m, calls, lines, syncs } = setup({ script: () => script, timeoutMs: 20 });
      const r = await m.run(halt("paper0"));
      expect(r.ok ? "ok" : r.error.kind).toBe(kind);
      expect(calls).toHaveLength(1);
      expect(syncs()).toBe(0);
      expect(lines.some((l) => l.startsWith("warn halt"))).toBe(true);
    }
  });

  it("explains a sudo password prompt", () => {
    const c = config(tmp(), { user: "root" });
    const e = classifyRun({ code: 1, stderr: "sudo: a password is required\n" }, c, "halt");
    expect(e.message).toMatch(/without a password/);
  });

  it("reports a failed follow-up sync but keeps the success", async () => {
    const { m } = setup({ sync: false });
    const r = await m.run(halt("*"));
    expect(r.ok && r.synced).toBe(false);
    expect(r.ok).toBe(true);
  });
});
