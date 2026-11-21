/**
 * DESK-019: is a newer NEM Desk release published? GET-only, no auth, one allowlisted URL
 * (the repo's GitHub Releases list), redirects refused. Errors are values; nothing throws.
 * Never downloads or installs anything: the result only carries the release page URL.
 */
import {
  DESK_TAG_PREFIX,
  RELEASE_PAGE_PREFIX,
  UPDATE_API_HOST,
  UPDATE_RELEASES_URL,
  type ReleaseInfo,
  type UpdateCheck,
  type UpdateError,
} from "@shared/updates";

export interface Semver {
  major: number;
  minor: number;
  patch: number;
  pre: string[];
}

const SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z.-]+)?$/;

export function parseSemver(raw: string): Semver | null {
  const m = SEMVER.exec(raw.trim());
  if (m === null) return null;
  return {
    major: Number(m[1]),
    minor: Number(m[2]),
    patch: Number(m[3]),
    pre: m[4] === undefined ? [] : m[4].split("."),
  };
}

function comparePre(a: string[], b: string[]): number {
  // A release (no pre-release part) is newer than any pre-release of the same version.
  if (a.length === 0 || b.length === 0) return a.length === b.length ? 0 : a.length === 0 ? 1 : -1;
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i];
    const y = b[i];
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    const xn = /^\d+$/.test(x);
    const yn = /^\d+$/.test(y);
    if (xn && yn) {
      const d = Number(x) - Number(y);
      if (d !== 0) return Math.sign(d);
    } else if (xn !== yn) {
      return xn ? -1 : 1;
    } else if (x !== y) {
      return x < y ? -1 : 1;
    }
  }
  return 0;
}

/** Semver precedence: negative if a < b, 0 if equal, positive if a > b. */
export function compareSemver(a: Semver, b: Semver): number {
  return (
    Math.sign(a.major - b.major) ||
    Math.sign(a.minor - b.minor) ||
    Math.sign(a.patch - b.patch) ||
    comparePre(a.pre, b.pre)
  );
}

export function isAllowedReleasePage(raw: unknown): raw is string {
  if (typeof raw !== "string") return false;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false;
  }
  return (
    url.protocol === "https:" &&
    url.host === "github.com" &&
    url.username === "" &&
    url.password === "" &&
    url.href.startsWith(RELEASE_PAGE_PREFIX)
  );
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * The newest published `desk-v<semver>` release (drafts, pre-releases, other tags and
 * pre-release versions ignored), or null. Throws only for a body that isn't a list.
 */
export function pickLatest(body: unknown): ReleaseInfo | null {
  if (!Array.isArray(body)) throw new TypeError("expected a JSON array of releases");
  let best: { info: ReleaseInfo; v: Semver } | null = null;
  for (const r of body) {
    if (!isRecord(r) || r["draft"] !== false || r["prerelease"] !== false) continue;
    const tag = r["tag_name"];
    if (typeof tag !== "string" || !tag.startsWith(DESK_TAG_PREFIX)) continue;
    const version = tag.slice(DESK_TAG_PREFIX.length);
    const v = parseSemver(version);
    if (v === null || v.pre.length > 0) continue;
    // Link only to this repo's release page; fall back to the tag's page if html_url is odd.
    const html = r["html_url"];
    const url = isAllowedReleasePage(html) ? html : `${RELEASE_PAGE_PREFIX}tag/${encodeURIComponent(tag)}`;
    const name = typeof r["name"] === "string" && r["name"] !== "" ? r["name"] : tag;
    const publishedAt = typeof r["published_at"] === "string" ? r["published_at"] : null;
    if (best === null || compareSemver(v, best.v) > 0) best = { info: { version, tag, name, url, publishedAt }, v };
  }
  return best?.info ?? null;
}

export interface FetchResponse {
  status: number;
  json(): Promise<unknown>;
}
export type FetchLike = (
  url: string,
  init: { method: "GET"; headers: Record<string, string>; redirect: "error"; signal?: AbortSignal },
) => Promise<FetchResponse>;

/** Exactly the releases list URL; anything else is refused. */
export function assertAllowedUrl(raw: string): URL | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  const base = new URL(UPDATE_RELEASES_URL);
  if (
    url.protocol !== "https:" ||
    url.host !== UPDATE_API_HOST ||
    url.username !== "" ||
    url.password !== "" ||
    url.pathname !== base.pathname
  ) {
    return null;
  }
  return url;
}

export interface CheckOptions {
  fetch: FetchLike;
  current: string;
  userAgent: string;
  now?: () => Date;
  timeoutMs?: number;
  /** Overridable only so tests can prove other URLs are blocked. */
  url?: string;
}

export async function checkForUpdate(opts: CheckOptions): Promise<UpdateCheck> {
  const at = (opts.now ?? (() => new Date()))().toISOString();
  const { current } = opts;
  const fail = (error: UpdateError): UpdateCheck => ({ ok: false, at, current, error });

  const url = assertAllowedUrl(opts.url ?? `${UPDATE_RELEASES_URL}?per_page=30`);
  if (url === null) return fail({ kind: "blocked", message: `request blocked (only ${UPDATE_RELEASES_URL})` });

  let res: FetchResponse;
  try {
    res = await opts.fetch(url.toString(), {
      method: "GET",
      headers: {
        Accept: "application/vnd.github+json",
        "User-Agent": opts.userAgent,
        "X-GitHub-Api-Version": "2022-11-28",
      },
      redirect: "error",
      signal: AbortSignal.timeout(opts.timeoutMs ?? 10_000),
    });
  } catch (e) {
    return fail({ kind: "offline", message: `GitHub unreachable: ${e instanceof Error ? e.message : String(e)}` });
  }
  // Unauthenticated GitHub API limit is 60 requests/hour per IP; it answers 403 or 429.
  if (res.status === 403 || res.status === 429) {
    return fail({ kind: "rate_limited", message: `GitHub rate limit (HTTP ${res.status})`, status: res.status });
  }
  if (res.status !== 200) {
    return fail({ kind: "upstream", message: `GitHub answered HTTP ${res.status}`, status: res.status });
  }
  let latest: ReleaseInfo | null;
  try {
    latest = pickLatest(await res.json());
  } catch (e) {
    return fail({ kind: "invalid_response", message: `unexpected response: ${e instanceof Error ? e.message : String(e)}` });
  }
  const cur = parseSemver(current);
  const lv = latest === null ? null : parseSemver(latest.version);
  const updateAvailable = cur !== null && lv !== null && compareSemver(lv, cur) > 0;
  return { ok: true, at, current, latest, updateAvailable };
}
