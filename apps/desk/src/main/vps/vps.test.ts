// @vitest-environment node
import { EventEmitter } from "node:events";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { DEFAULT_REMOTE_BASE, type VpsConfig } from "@shared/vps";
import { classify, rsyncArgs, sshCommand, validateConfig, VpsManager, type ProcLike, type SpawnLike } from "./sync";

/** No test contacts a server: `spawn` is always this fake. */
interface Call {
  cmd: string;
  args: readonly string[];
  shell: boolean;
}

function fakeSpawn(script: (call: Call, n: number) => { code: number | null; stderr?: string; error?: string }): {
  spawn: SpawnLike;
  calls: Call[];
} {
  const calls: Call[] = [];
  const spawn: SpawnLike = (cmd, args, options) => {
    const call = { cmd, args, shell: options.shell };
    calls.push(call);
    const result = script(call, calls.length - 1);
    const proc = new EventEmitter() as EventEmitter & ProcLike;
    const stderr = new EventEmitter();
    Object.assign(proc, { stderr, stdout: new EventEmitter(), kill: () => true });
    setTimeout(() => {
      if (result.error !== undefined) {
        proc.emit("error", Object.assign(new Error(result.error), { code: result.error }));
        return;
      }
      if (result.stderr !== undefined) stderr.emit("data", Buffer.from(result.stderr));
      proc.emit("close", result.code);
    }, 0);
    return proc;
  };
  return { spawn, calls };
}

const tmps: string[] = [];
function tmp(): string {
  const d = mkdtempSync(join(tmpdir(), "nem-desk-vps-"));
  tmps.push(d);
  return d;
}
afterEach(() => {
  for (const d of tmps.splice(0)) rmSync(d, { recursive: true, force: true });
});

function keyFile(dir: string): string {
  const p = join(dir, "id_ed25519");
  writeFileSync(p, "not a real key");
  return p;
}

function config(dir: string, over: Partial<VpsConfig> = {}): VpsConfig {
  return { host: "nem.example.org", user: "root", port: 22, keyPath: keyFile(dir), remoteBase: DEFAULT_REMOTE_BASE, intervalMinutes: null, ...over };
}

describe("validateConfig", () => {
  it("accepts a normal config and defaults port and remote base", () => {
    const d = tmp();
    const v = validateConfig({ host: "1.2.3.4", user: "nem", keyPath: keyFile(d) });
    expect(v.ok && v.config).toMatchObject({ port: 22, remoteBase: DEFAULT_REMOTE_BASE, intervalMinutes: null });
  });

  it.each([
    ["host", { host: "-oProxyCommand=evil" }],
    ["host", { host: "a b" }],
    ["host", { host: "" }],
    ["user", { user: "root; rm -rf /" }],
    ["user", { user: "-l" }],
    ["port", { port: 0 }],
    ["keyPath", { keyPath: "relative/key" }],
    ["keyPath", { keyPath: "/no such/key" }],
    ["keyPath", { keyPath: "/definitely/missing/key" }],
    ["remoteBase", { remoteBase: "/home/nem/../../etc" }],
    ["remoteBase", { remoteBase: "/home/$(whoami)" }],
    ["intervalMinutes", { intervalMinutes: 1 }],
  ] as const)("rejects a bad %s", (field, over) => {
    const v = validateConfig({ ...config(tmp()), ...over });
    expect(v.ok ? null : v.error).toMatchObject({ kind: "invalid", field });
  });
});

describe("ssh and rsync arguments", () => {
  it("verifies host keys strictly, never prompts, and ends options with --", () => {
    const d = tmp();
    const c = config(d, { port: 2222 });
    const ssh = sshCommand(c);
    expect(ssh).toContain("-o StrictHostKeyChecking=yes");
    expect(ssh).toContain("-o BatchMode=yes");
    expect(ssh).toContain("-o PasswordAuthentication=no");
    expect(ssh).toContain(`-i ${c.keyPath}`);
    expect(ssh).toContain("-p 2222");
    expect(ssh).not.toMatch(/accept-new|StrictHostKeyChecking=no|UserKnownHostsFile/);
    const [out, db] = rsyncArgs(c, "/cache");
    expect(out?.args.slice(-3)).toEqual(["--", `root@nem.example.org:${DEFAULT_REMOTE_BASE}/out/`, "/cache/out/"]);
    expect(db?.args.slice(-3)).toEqual(["--", `root@nem.example.org:${DEFAULT_REMOTE_BASE}/data/snapshot/nem.db`, "/cache/nem.db"]);
    expect(out?.args).toContain("--delete");
    expect(db?.args).not.toContain("--delete");
  });
});

describe("classify", () => {
  const c = { host: "h", user: "u", port: 22, keyPath: "/k", remoteBase: "/r", intervalMinutes: null };
  it.each([
    ["host_key", 255, "Host key verification failed.\r\nrsync: connection unexpectedly closed"],
    ["host_key", 255, "No ED25519 host key is known for h and you have requested strict checking."],
    ["host_key", 255, "@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @"],
    ["auth", 255, "u@h: Permission denied (publickey)."],
    ["unreachable", 255, "ssh: Could not resolve hostname h: nodename nor servname provided"],
    ["unreachable", 255, "ssh: connect to host h port 22: Connection refused"],
    ["remote_missing", 23, 'rsync: link_stat "/r/data/snapshot/nem.db" failed: No such file or directory (2)'],
    ["timeout", 30, "rsync error: timeout in data send/receive (code 30)"],
    ["failed", 12, "rsync: protocol mismatch"],
  ] as const)("%s", (kind, code, stderr) => {
    expect(classify("db", code, stderr, c).kind).toBe(kind);
  });

  it("points at the snapshot timer when the remote DB is missing", () => {
    expect(classify("db", 23, "No such file or directory", c).message).toContain("RUNBOOK");
  });
});

describe("VpsManager", () => {
  function manager(spawn: SpawnLike, synced: string[] = []) {
    const d = tmp();
    const m = new VpsManager({
      storePath: join(d, "vps.json"),
      cacheDir: join(d, "cache"),
      spawn,
      timers: false,
      onSynced: (dir) => synced.push(dir),
      now: () => new Date("2026-11-20T12:00:00Z"),
    });
    return { m, d };
  }

  it("needs a config before syncing", async () => {
    const { spawn, calls } = fakeSpawn(() => ({ code: 0 }));
    const { m } = manager(spawn);
    const r = await m.sync();
    expect(r.error?.kind).toBe("not_configured");
    expect(calls).toHaveLength(0);
  });

  it("saves only paths and names (0600), and syncs out/ then the DB with no shell", async () => {
    const synced: string[] = [];
    const { spawn, calls } = fakeSpawn(() => ({ code: 0 }));
    const { m, d } = manager(spawn, synced);
    const save = m.save(config(d));
    expect(save.ok).toBe(true);
    const stored = readFileSync(join(d, "vps.json"), "utf8");
    expect(stored).toContain("nem.example.org");
    expect(stored).not.toContain("not a real key");
    const r = await m.sync();
    expect(r.ok).toBe(true);
    expect(r.steps.map((s) => s.what)).toEqual(["out", "db"]);
    expect(calls.map((c) => c.cmd)).toEqual(["rsync", "rsync"]);
    expect(calls.every((c) => !c.shell)).toBe(true);
    expect(synced).toEqual([join(d, "cache")]);
    expect(existsSync(join(d, "cache", "out"))).toBe(true);
    expect(m.state().lastSync?.at).toBe("2026-11-20T12:00:00.000Z");
  });

  it("fails closed on an unknown host key: stops, adds no source", async () => {
    const synced: string[] = [];
    const { spawn, calls } = fakeSpawn(() => ({ code: 255, stderr: "Host key verification failed.\n" }));
    const { m, d } = manager(spawn, synced);
    m.save(config(d));
    const r = await m.sync();
    expect(r.ok).toBe(false);
    expect(r.error?.kind).toBe("host_key");
    expect(r.error?.message).toContain("never accepts unknown keys");
    expect(calls).toHaveLength(1);
    expect(synced).toEqual([]);
  });

  it("a missing remote snapshot still brings out/ down", async () => {
    const synced: string[] = [];
    const { spawn } = fakeSpawn((_c, n) => (n === 0 ? { code: 0 } : { code: 23, stderr: "link_stat failed: No such file or directory" }));
    const { m, d } = manager(spawn, synced);
    m.save(config(d));
    const r = await m.sync();
    expect(r.ok).toBe(false);
    expect(r.steps.map((s) => s.ok)).toEqual([true, false]);
    expect(r.error?.kind).toBe("remote_missing");
    expect(synced).toHaveLength(1);
  });

  it("reports a missing rsync", async () => {
    const { spawn } = fakeSpawn(() => ({ code: null, error: "ENOENT" }));
    const { m, d } = manager(spawn);
    m.save(config(d));
    expect((await m.sync()).error?.kind).toBe("missing_tool");
  });

  it("shares one run between concurrent syncs and forgets the config", async () => {
    const { spawn, calls } = fakeSpawn(() => ({ code: 0 }));
    const { m, d } = manager(spawn);
    m.save(config(d));
    const [a, b] = await Promise.all([m.sync(), m.sync()]);
    expect(a).toBe(b);
    expect(calls).toHaveLength(2);
    expect(m.forget().config).toBeNull();
  });

  it("rejects an invalid save without touching the stored config", () => {
    const { spawn } = fakeSpawn(() => ({ code: 0 }));
    const { m, d } = manager(spawn);
    m.save(config(d));
    const bad = m.save({ ...config(d), host: "bad host" });
    expect(bad.ok ? null : bad.error.field).toBe("host");
    expect(m.state().config?.host).toBe("nem.example.org");
  });
});
