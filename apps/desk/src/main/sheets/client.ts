/**
 * Read-only fetch of one Google Sheets tab as CSV (DESK-012), with no Google account: the
 * sheet must be shared "Anyone with the link: Viewer". GET only, no cookies or auth
 * headers, and only https://docs.google.com/spreadsheets/d/<id>/gviz/tq is ever requested.
 * The gviz endpoint answers directly (no redirect), so redirects are not followed: one to
 * accounts.google.com means the sheet is private, anything else is refused. Every failure
 * is a `SheetsError` value; nothing here throws.
 *
 * The sheet ID never appears in an error message: messages name the tab only.
 */
import type { SheetsError } from "@shared/sheets";
import { RateLimiter } from "../kalshi/client";

export const SHEETS_HOST = "docs.google.com";
/** Hosts a redirect may point at that we recognize (and never follow). */
const SIGN_IN_HOSTS = ["accounts.google.com"];
const ID_RE = /^[A-Za-z0-9_-]{20,128}$/;
export const MAX_TAB_BYTES = 64 * 1024 * 1024;

export type ParsedRef = { ok: true; id: string } | { ok: false; error: SheetsError };

/** Accepts a share link (`.../spreadsheets/d/<id>/edit#gid=0`) or a bare ID. */
export function parseSheetRef(input: unknown): ParsedRef {
  const bad = (message: string): ParsedRef => ({ ok: false, error: { kind: "invalid", field: "sheet", message } });
  if (typeof input !== "string") return bad("Paste the sheet's link or ID.");
  const s = input.trim();
  if (s === "" || s.length > 2048) return bad("Paste the sheet's link or ID.");
  if (ID_RE.test(s)) return { ok: true, id: s };
  let url: URL;
  try {
    url = new URL(s);
  } catch {
    return bad("Not a Google Sheets link or ID. Use the share link: docs.google.com/spreadsheets/d/<id>/...");
  }
  if (url.protocol !== "https:" || url.host !== SHEETS_HOST) {
    return bad("Only docs.google.com spreadsheet links are accepted.");
  }
  const m = /^\/spreadsheets\/(?:u\/\d+\/)?d\/([^/]+)/.exec(url.pathname);
  if (m?.[1] === undefined) return bad("Not a spreadsheet link: expected docs.google.com/spreadsheets/d/<id>/...");
  if (m[1] === "e") {
    return bad('That is a "Publish to the web" link. Use the share link instead (Share, "Anyone with the link: Viewer").');
  }
  if (!ID_RE.test(m[1])) return bad("The link's sheet ID looks malformed.");
  return { ok: true, id: m[1] };
}

export class HostNotAllowedError extends Error {}

/** Builds the one URL shape we fetch; anything else is a programming error. */
export function tabUrl(id: string, tab: string): string {
  if (!ID_RE.test(id)) throw new HostNotAllowedError("bad sheet id");
  const url = new URL(`https://${SHEETS_HOST}/spreadsheets/d/${id}/gviz/tq`);
  url.searchParams.set("tqx", "out:csv");
  // Exactly one header row (gviz otherwise guesses how many rows are headers).
  url.searchParams.set("headers", "1");
  url.searchParams.set("sheet", tab);
  return assertAllowedUrl(url.toString()).toString();
}

export function assertAllowedUrl(raw: string): URL {
  const url = new URL(raw);
  if (
    url.protocol !== "https:" ||
    url.host !== SHEETS_HOST ||
    url.username !== "" ||
    url.password !== "" ||
    !/^\/spreadsheets\/d\/[A-Za-z0-9_-]+\/gviz\/tq$/.test(url.pathname)
  ) {
    throw new HostNotAllowedError("request blocked: only docs.google.com/spreadsheets/d/<id>/gviz/tq");
  }
  return url;
}

export interface SheetsResponse {
  status: number;
  headers: { get(name: string): string | null };
  text(): Promise<string>;
}

export type SheetsFetch = (
  url: string,
  init: {
    method: "GET";
    headers: Record<string, string>;
    redirect: "manual";
    credentials: "omit";
    signal?: AbortSignal;
  },
) => Promise<SheetsResponse>;

export interface TabClientOptions {
  fetch: SheetsFetch;
  userAgent: string;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  /** Requests per second (default 1). */
  perSecond?: number;
  maxRetries?: number;
  timeoutMs?: number;
}

export type TabResult = { ok: true; text: string } | { ok: false; error: SheetsError };

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function fail(kind: SheetsError["kind"], message: string, status?: number): { ok: false; error: SheetsError } {
  return { ok: false, error: status === undefined ? { kind, message } : { kind, message, status } };
}

const SHARE_HINT = 'In Google Sheets: Share, General access "Anyone with the link", role Viewer.';

export class SheetsClient {
  private readonly limiter: RateLimiter;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly maxRetries: number;
  private readonly timeoutMs: number;

  constructor(private readonly opts: TabClientOptions) {
    this.sleep = opts.sleep ?? defaultSleep;
    this.limiter = new RateLimiter(opts.perSecond ?? 1, opts.now ?? Date.now, this.sleep);
    this.maxRetries = opts.maxRetries ?? 2;
    this.timeoutMs = opts.timeoutMs ?? 30_000;
  }

  /** One tab's CSV text, with retries (2s, 4s) on 429 / 5xx / network errors. */
  async tab(id: string, tab: string): Promise<TabResult> {
    let href: string;
    try {
      href = tabUrl(id, tab);
    } catch (e) {
      return fail("blocked", e instanceof Error ? e.message : String(e));
    }
    const what = `tab "${tab}"`;
    let last: { ok: false; error: SheetsError } = fail("offline", "no attempt made");
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      if (attempt > 0) await this.sleep(2000 * 2 ** (attempt - 1));
      await this.limiter.acquire();
      let res: SheetsResponse;
      try {
        res = await this.opts.fetch(href, {
          method: "GET",
          headers: { Accept: "text/csv", "User-Agent": this.opts.userAgent },
          redirect: "manual",
          credentials: "omit",
          signal: AbortSignal.timeout(this.timeoutMs),
        });
      } catch (e) {
        last = fail("offline", `Could not reach Google Sheets for ${what} (${e instanceof Error ? e.message : String(e)}).`);
        continue;
      }
      const s = res.status;
      if (s === 429) {
        last = fail("rate_limited", `Google Sheets is rate limiting requests (HTTP 429) for ${what}.`, s);
        continue;
      }
      if (s >= 500) {
        last = fail("upstream", `Google Sheets answered HTTP ${s} for ${what}.`, s);
        continue;
      }
      if (s >= 300 && s < 400) {
        const loc = res.headers.get("location") ?? "";
        let host = "";
        try {
          host = new URL(loc, href).host;
        } catch {
          // unparseable location
        }
        if (SIGN_IN_HOSTS.includes(host) || /ServiceLogin|signin/i.test(loc)) {
          return fail("not_shared", `The sheet is private: Google asked for a sign-in. ${SHARE_HINT}`, s);
        }
        return fail("blocked", `Google redirected ${what} to ${host || "an unknown place"}; NEM Desk only reads docs.google.com and does not follow redirects.`, s);
      }
      if (s === 401 || s === 403) return fail("not_shared", `The sheet is private (HTTP ${s}). ${SHARE_HINT}`, s);
      if (s === 404) {
        return fail("not_found", `Google has no sheet with this ID that it shows without signing in. Check the link, and: ${SHARE_HINT}`, s);
      }
      if (s >= 400) return fail("invalid_response", `Google Sheets answered HTTP ${s} for ${what}.`, s);
      const length = Number(res.headers.get("content-length"));
      if (Number.isFinite(length) && length > MAX_TAB_BYTES) {
        return fail("too_large", `${what} is larger than ${MAX_TAB_BYTES / 1024 / 1024} MB.`);
      }
      const type = (res.headers.get("content-type") ?? "").toLowerCase();
      if (type.includes("text/html")) {
        return fail("not_shared", `Google answered with a web page instead of CSV for ${what} (usually a sign-in page). ${SHARE_HINT}`);
      }
      let text: string;
      try {
        text = await res.text();
      } catch (e) {
        last = fail("offline", `Reading ${what} failed (${e instanceof Error ? e.message : String(e)}).`);
        continue;
      }
      if (text.length > MAX_TAB_BYTES) return fail("too_large", `${what} is larger than ${MAX_TAB_BYTES / 1024 / 1024} MB.`);
      if (!type.includes("csv") && /^\s*</.test(text)) {
        return fail("not_shared", `Google answered with a web page instead of CSV for ${what}. ${SHARE_HINT}`);
      }
      return { ok: true, text };
    }
    return { ok: false, error: { ...last.error, message: `${last.error.message} Gave up after ${this.maxRetries + 1} tries.` } };
  }
}
