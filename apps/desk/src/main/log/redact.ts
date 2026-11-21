/**
 * Redaction for anything logged or copied (DESK-020). Two layers:
 *
 * 1. Known private values (the sheet ID, the VPS host, user and key path, the userData and
 *    home folders, the OS user name) are replaced by placeholders, longest first.
 * 2. Patterns catch what was never registered: Google sheet links, long opaque tokens,
 *    private key blocks, `user@host`, IPv4 addresses and absolute home paths.
 */

export interface Secret {
  value: string;
  placeholder: string;
}

const MIN_SECRET = 3;

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const PATTERNS: [RegExp, string][] = [
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)/g, "<private-key>"],
  [/(\/spreadsheets\/d\/)(?:e\/)?[A-Za-z0-9_-]{10,}/g, "$1<sheet-id>"],
  // Sheet IDs are 44 chars; API keys, tokens and hashes are similar runs.
  [/(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{40,}(?![A-Za-z0-9_-])/g, "<token>"],
  [/(?:key|token|secret|password|passwd|auth)=([^\s&"']+)/gi, "<redacted-param>"],
  [/\b[A-Za-z0-9_.-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b/g, "<user>@<host>"],
  [/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, "<ip>"],
  [/(\/Users\/|\/home\/)[^/\s"']+/g, "$1<user>"],
  [/([A-Za-z]:\\Users\\)[^\\\s"']+/g, "$1<user>"],
  [/[^\s"']*\.ssh[/\\][^\s"']*/g, "<key-path>"],
];

export class Redactor {
  private providers: (() => Secret[])[] = [];

  /** Adds a source of private values; it is asked again on every redaction. */
  addProvider(p: () => Secret[]): void {
    this.providers.push(p);
  }

  secrets(): Secret[] {
    const out: Secret[] = [];
    for (const p of this.providers) {
      try {
        out.push(...p());
      } catch {
        // a failing provider must not stop logging
      }
    }
    return out
      .filter((s) => s.value.trim().length >= MIN_SECRET)
      .sort((a, b) => b.value.length - a.value.length);
  }

  redact(text: string): string {
    let s = text;
    for (const { value, placeholder } of this.secrets()) {
      s = s.replace(new RegExp(escapeRe(value), "g"), placeholder);
    }
    for (const [re, to] of PATTERNS) s = s.replace(re, to);
    return s;
  }
}

/** `1AbC…9xYz`: enough to tell two sheets apart, not enough to open one. */
export function maskId(id: string): string {
  return id.length <= 8 ? "…" : `${id.slice(0, 4)}…${id.slice(-4)}`;
}
