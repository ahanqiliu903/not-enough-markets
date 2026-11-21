/**
 * DESK-019 "update available" notice. Releases are unsigned, so there is no auto-update:
 * main asks GitHub Releases (GET only, no auth) whether a newer `desk-v*` release exists
 * and the UI links to its page. Nothing is ever downloaded or installed.
 */

export const UPDATE_REPO = "ahanqiliu903/not-enough-markets";
export const UPDATE_API_HOST = "api.github.com";
export const UPDATE_RELEASES_URL = `https://${UPDATE_API_HOST}/repos/${UPDATE_REPO}/releases`;
/** Only release pages under this prefix may be opened in the browser. */
export const RELEASE_PAGE_PREFIX = `https://github.com/${UPDATE_REPO}/releases/`;
export const DESK_TAG_PREFIX = "desk-v";
/** Automatic checks run at most this often. */
export const CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000;

export type UpdateErrorKind = "offline" | "rate_limited" | "upstream" | "invalid_response" | "blocked";

export interface UpdateError {
  kind: UpdateErrorKind;
  message: string;
  status?: number;
}

export interface ReleaseInfo {
  /** Version without the tag prefix, e.g. "0.2.0". */
  version: string;
  tag: string;
  name: string;
  /** Allowlisted https://github.com/<repo>/releases/... page. */
  url: string;
  publishedAt: string | null;
}

export type UpdateCheck =
  | {
      ok: true;
      at: string;
      current: string;
      /** Newest published desk release, or null when there is none yet. */
      latest: ReleaseInfo | null;
      updateAvailable: boolean;
    }
  | { ok: false; at: string; current: string; error: UpdateError };

export interface UpdateState {
  /** Automatic daily checks; "Check now" works either way. */
  enabled: boolean;
  current: string;
  checking: boolean;
  last: UpdateCheck | null;
}

export interface UpdatesApi {
  get: () => Promise<UpdateState>;
  /** Manual "Check now". */
  check: () => Promise<UpdateState>;
  setEnabled: (enabled: boolean) => Promise<UpdateState>;
  /** Opens the last found newer release's page in the system browser. False if none. */
  openRelease: () => Promise<boolean>;
}
