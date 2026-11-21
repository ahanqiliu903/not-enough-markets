// @vitest-environment node
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { CHECK_INTERVAL_MS, UPDATE_RELEASES_URL } from "@shared/updates";
import {
  assertAllowedUrl,
  checkForUpdate,
  compareSemver,
  isAllowedReleasePage,
  parseSemver,
  pickLatest,
  type FetchLike,
} from "./check";
import { UpdateManager } from "./manager";

/** No test touches the network: fetch is always this fake. */
function fakeFetch(respond: () => { status: number; body?: unknown } | Error): { fetch: FetchLike; calls: { url: string; init: Parameters<FetchLike>[1] }[] } {
  const calls: { url: string; init: Parameters<FetchLike>[1] }[] = [];
  const fetch: FetchLike = (url, init) => {
    calls.push({ url, init });
    const r = respond();
    if (r instanceof Error) return Promise.reject(r);
    return Promise.resolve({ status: r.status, json: () => Promise.resolve(r.body) });
  };
  return { fetch, calls };
}

const REPO_PAGE = "https://github.com/ahanqiliu903/not-enough-markets/releases/tag/";

function release(tag: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    tag_name: tag,
    name: `NEM Desk ${tag}`,
    draft: false,
    prerelease: false,
    html_url: `${REPO_PAGE}${tag}`,
    published_at: "2026-11-01T00:00:00Z",
    // Fields we must never use: assets are for downloading, which we don't do.
    assets: [{ browser_download_url: "https://example.com/x.dmg" }],
    ...extra,
  };
}

describe("semver", () => {
  const v = (s: string) => {
    const p = parseSemver(s);
    if (p === null) throw new Error(s);
    return p;
  };
  it("parses and rejects", () => {
    expect(v("1.2.3")).toEqual({ major: 1, minor: 2, patch: 3, pre: [] });
    expect(v("1.2.3-beta.1+build.5").pre).toEqual(["beta", "1"]);
    for (const bad of ["1.2", "v1.2.3", "01.2.3", "1.2.3.4", "", "x"]) expect(parseSemver(bad)).toBeNull();
  });
  it("orders by precedence", () => {
    const ordered = ["0.1.0", "0.1.1", "0.2.0-alpha", "0.2.0-alpha.1", "0.2.0-alpha.beta", "0.2.0-beta.2", "0.2.0-beta.11", "0.2.0", "0.10.0", "1.0.0"];
    for (let i = 1; i < ordered.length; i++) {
      expect(compareSemver(v(ordered[i - 1] ?? ""), v(ordered[i] ?? ""))).toBeLessThan(0);
      expect(compareSemver(v(ordered[i] ?? ""), v(ordered[i - 1] ?? ""))).toBeGreaterThan(0);
    }
    expect(compareSemver(v("1.0.0+a"), v("1.0.0+b"))).toBe(0);
  });
});

describe("pickLatest", () => {
  it("takes the highest published desk-v release and ignores everything else", () => {
    const body = [
      release("desk-v0.3.0", { draft: true }),
      release("desk-v0.4.0", { prerelease: true }),
      release("desk-v0.5.0-rc.1"),
      release("v9.9.9"),
      release("desk-vnope"),
      release("desk-v0.2.0"),
      release("desk-v0.10.0"),
      release("desk-v0.9.1"),
      "junk",
      null,
    ];
    const latest = pickLatest(body);
    expect(latest).toEqual({
      version: "0.10.0",
      tag: "desk-v0.10.0",
      name: "NEM Desk desk-v0.10.0",
      url: `${REPO_PAGE}desk-v0.10.0`,
      publishedAt: "2026-11-01T00:00:00Z",
    });
  });
  it("never links outside the repo's release pages", () => {
    const latest = pickLatest([release("desk-v1.0.0", { html_url: "https://evil.example/releases/x" })]);
    expect(latest?.url).toBe(`${REPO_PAGE}desk-v1.0.0`);
  });
  it("returns null with no desk release, throws on a non-list", () => {
    expect(pickLatest([])).toBeNull();
    expect(() => pickLatest({ message: "Not Found" })).toThrow();
  });
});

describe("allowlists", () => {
  it("only requests the releases list on api.github.com over https", () => {
    expect(assertAllowedUrl(`${UPDATE_RELEASES_URL}?per_page=30`)).not.toBeNull();
    for (const bad of [
      "http://api.github.com/repos/ahanqiliu903/not-enough-markets/releases",
      "https://api.github.com/repos/someone/else/releases",
      "https://api.github.com/repos/ahanqiliu903/not-enough-markets/releases/assets/1",
      "https://user:pw@api.github.com/repos/ahanqiliu903/not-enough-markets/releases",
      "https://api.github.com.evil.example/repos/ahanqiliu903/not-enough-markets/releases",
      "not a url",
    ]) {
      expect(assertAllowedUrl(bad)).toBeNull();
    }
  });
  it("only opens https://github.com release pages of this repo", () => {
    expect(isAllowedReleasePage(`${REPO_PAGE}desk-v1.0.0`)).toBe(true);
    for (const bad of [
      "http://github.com/ahanqiliu903/not-enough-markets/releases/tag/x",
      "https://github.com/other/repo/releases/tag/x",
      "https://github.com.evil.example/ahanqiliu903/not-enough-markets/releases/",
      "https://x@github.com/ahanqiliu903/not-enough-markets/releases/tag/x",
      "file:///etc/passwd",
      "javascript:alert(1)",
      42,
    ]) {
      expect(isAllowedReleasePage(bad)).toBe(false);
    }
  });
});

describe("checkForUpdate", () => {
  const now = () => new Date("2026-11-20T12:00:00Z");
  const base = { current: "0.1.0", userAgent: "NEM Desk/0.1.0", now };

  it("sends one unauthenticated GET with redirects refused", async () => {
    const { fetch, calls } = fakeFetch(() => ({ status: 200, body: [release("desk-v0.2.0")] }));
    const r = await checkForUpdate({ ...base, fetch });
    expect(r).toMatchObject({ ok: true, updateAvailable: true, latest: { version: "0.2.0" }, at: "2026-11-20T12:00:00.000Z" });
    expect(calls).toHaveLength(1);
    const call = calls[0];
    expect(call?.url.startsWith(UPDATE_RELEASES_URL)).toBe(true);
    expect(call?.init.method).toBe("GET");
    expect(call?.init.redirect).toBe("error");
    expect(Object.keys(call?.init.headers ?? {}).map((h) => h.toLowerCase())).not.toContain("authorization");
  });
  it("is not an update when equal or older", async () => {
    for (const tag of ["desk-v0.1.0", "desk-v0.0.9"]) {
      const { fetch } = fakeFetch(() => ({ status: 200, body: [release(tag)] }));
      expect(await checkForUpdate({ ...base, fetch })).toMatchObject({ ok: true, updateAvailable: false });
    }
    const { fetch } = fakeFetch(() => ({ status: 200, body: [] }));
    expect(await checkForUpdate({ ...base, fetch })).toMatchObject({ ok: true, latest: null, updateAvailable: false });
  });
  it("a pre-release app sees the final release as newer", async () => {
    const { fetch } = fakeFetch(() => ({ status: 200, body: [release("desk-v0.2.0")] }));
    expect(await checkForUpdate({ ...base, current: "0.2.0-beta.1", fetch })).toMatchObject({ updateAvailable: true });
  });
  it("returns errors as values", async () => {
    const cases: [ReturnType<Parameters<typeof fakeFetch>[0]>, string][] = [
      [new Error("ENOTFOUND"), "offline"],
      [{ status: 403 }, "rate_limited"],
      [{ status: 429 }, "rate_limited"],
      [{ status: 500 }, "upstream"],
      [{ status: 404 }, "upstream"],
      [{ status: 200, body: { message: "nope" } }, "invalid_response"],
    ];
    for (const [resp, kind] of cases) {
      const { fetch } = fakeFetch(() => resp);
      const r = await checkForUpdate({ ...base, fetch });
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.error.kind).toBe(kind);
    }
    const { fetch, calls } = fakeFetch(() => ({ status: 200, body: [] }));
    const blocked = await checkForUpdate({ ...base, fetch, url: "https://example.com/repos/x/releases" });
    expect(blocked).toMatchObject({ ok: false, error: { kind: "blocked" } });
    expect(calls).toHaveLength(0);
  });
});

describe("UpdateManager", () => {
  const dirs: string[] = [];
  afterEach(() => {
    for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
  });
  function setup(o: { autoCheck?: boolean; current?: string; body?: unknown } = {}) {
    const dir = mkdtempSync(join(tmpdir(), "nem-desk-updates-"));
    dirs.push(dir);
    let t = new Date("2026-11-20T12:00:00Z").getTime();
    const f = fakeFetch(() => ({ status: 200, body: o.body ?? [release("desk-v0.2.0")] }));
    const timers: { ms: number; fn: () => void }[] = [];
    const make = (current = o.current ?? "0.1.0") =>
      new UpdateManager({
        storePath: join(dir, "updates.json"),
        current,
        fetch: f.fetch,
        userAgent: "NEM Desk/test",
        autoCheck: o.autoCheck ?? true,
        now: () => new Date(t),
        setTimeout: (fn, ms) => timers.push({ fn, ms }),
        setInterval: (fn, ms) => timers.push({ fn, ms }),
      });
    return { dir, f, timers, make, advance: (ms: number) => (t += ms) };
  }

  it("checks automatically at most once per day, also across restarts", async () => {
    const s = setup();
    const m = s.make();
    expect(await m.maybeAutoCheck()).toBe(true);
    expect(await m.maybeAutoCheck()).toBe(false);
    s.advance(CHECK_INTERVAL_MS - 1000);
    expect(await s.make().maybeAutoCheck()).toBe(false);
    s.advance(1000);
    expect(await s.make().maybeAutoCheck()).toBe(true);
    expect(s.f.calls).toHaveLength(2);
  });
  it("disabled: no automatic checks, but Check now works; the setting persists", async () => {
    const s = setup();
    const m = s.make();
    expect(m.setEnabled(false).enabled).toBe(false);
    expect(await m.maybeAutoCheck()).toBe(false);
    expect(s.f.calls).toHaveLength(0);
    const st = await m.check();
    expect(st.last).toMatchObject({ ok: true, updateAvailable: true });
    expect(s.make().state().enabled).toBe(false);
    expect(JSON.parse(readFileSync(join(s.dir, "updates.json"), "utf8"))).toMatchObject({ enabled: false });
  });
  it("dev/test builds never check on their own and schedule nothing", async () => {
    const s = setup({ autoCheck: false });
    const m = s.make();
    m.start();
    expect(s.timers).toHaveLength(0);
    expect(await m.maybeAutoCheck()).toBe(false);
    expect(s.f.calls).toHaveLength(0);
  });
  it("start schedules a delayed first check and an hourly wake-up", () => {
    const s = setup();
    s.make().start();
    expect(s.timers.map((x) => x.ms)).toEqual([10_000, 60 * 60 * 1000]);
    expect(s.f.calls).toHaveLength(0);
  });
  it("de-duplicates concurrent checks", async () => {
    const s = setup();
    const m = s.make();
    await Promise.all([m.check(), m.check(), m.maybeAutoCheck()]);
    expect(s.f.calls).toHaveLength(1);
  });
  it("re-judges a stored result after the app was upgraded", async () => {
    const s = setup();
    await s.make("0.1.0").check();
    expect(s.make("0.1.0").releaseUrl()).toBe(`${REPO_PAGE}desk-v0.2.0`);
    const upgraded = s.make("0.2.0");
    expect(upgraded.state().last).toMatchObject({ ok: true, updateAvailable: false, current: "0.2.0" });
    expect(upgraded.releaseUrl()).toBeNull();
  });
  it("releaseUrl refuses a tampered store", () => {
    const s = setup();
    writeFileSync(
      join(s.dir, "updates.json"),
      JSON.stringify({
        enabled: true,
        last: {
          ok: true,
          at: "2026-11-20T12:00:00Z",
          current: "0.1.0",
          updateAvailable: true,
          latest: { version: "9.0.0", tag: "desk-v9.0.0", name: "x", url: "https://evil.example/", publishedAt: null },
        },
      }),
    );
    expect(s.make().releaseUrl()).toBeNull();
  });
  it("ignores an unreadable store and keeps errors in state", async () => {
    const s = setup({ body: { message: "bad" } });
    writeFileSync(join(s.dir, "updates.json"), "{not json");
    const m = s.make();
    expect(m.state()).toMatchObject({ enabled: true, last: null });
    const st = await m.check();
    expect(st.last).toMatchObject({ ok: false, error: { kind: "invalid_response" } });
    expect(m.releaseUrl()).toBeNull();
  });
});
