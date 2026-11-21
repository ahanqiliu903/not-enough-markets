/**
 * DESK-019: when to check, and what is remembered. Automatic checks run at most once per
 * CHECK_INTERVAL_MS (also across restarts: the last check is stored), only while enabled.
 * "Check now" is always allowed but de-duplicated while one is in flight.
 */
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { CHECK_INTERVAL_MS, type UpdateCheck, type UpdateState } from "@shared/updates";
import { checkForUpdate, compareSemver, isAllowedReleasePage, parseSemver, type FetchLike } from "./check";

interface Stored {
  enabled: boolean;
  last: UpdateCheck | null;
}

export interface ManagerOptions {
  storePath: string;
  current: string;
  fetch: FetchLike;
  userAgent: string;
  /** False in dev/test builds: no automatic network traffic there. */
  autoCheck: boolean;
  now?: () => Date;
  setInterval?: (fn: () => void, ms: number) => unknown;
  setTimeout?: (fn: () => void, ms: number) => unknown;
}

/** How often the scheduler wakes to see whether a check is due (cheap; no network). */
export const WAKE_MS = 60 * 60 * 1000;
const START_DELAY_MS = 10_000;

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isCheck(v: unknown): v is UpdateCheck {
  return isRecord(v) && typeof v["ok"] === "boolean" && typeof v["at"] === "string" && typeof v["current"] === "string";
}

export class UpdateManager {
  private stored: Stored;
  private inflight: Promise<UpdateCheck> | null = null;
  private readonly now: () => Date;

  constructor(private readonly opts: ManagerOptions) {
    this.now = opts.now ?? (() => new Date());
    this.stored = this.load();
  }

  private load(): Stored {
    try {
      const raw: unknown = JSON.parse(readFileSync(this.opts.storePath, "utf8"));
      if (isRecord(raw)) {
        return {
          enabled: typeof raw["enabled"] === "boolean" ? raw["enabled"] : true,
          last: isCheck(raw["last"]) ? raw["last"] : null,
        };
      }
    } catch {
      // Missing or unreadable: defaults.
    }
    return { enabled: true, last: null };
  }

  private save(): void {
    try {
      mkdirSync(dirname(this.opts.storePath), { recursive: true });
      const tmp = `${this.opts.storePath}.tmp`;
      writeFileSync(tmp, JSON.stringify(this.stored, null, 2), { mode: 0o600 });
      renameSync(tmp, this.opts.storePath);
    } catch {
      // Not persisting only means the next start may check again.
    }
  }

  /** The last check, re-judged against the running version (it may have been upgraded). */
  state(): UpdateState {
    const { current } = this.opts;
    let last = this.stored.last;
    if (last?.ok === true) {
      const cur = parseSemver(current);
      const lv = last.latest === null ? null : parseSemver(last.latest.version);
      last = { ...last, current, updateAvailable: cur !== null && lv !== null && compareSemver(lv, cur) > 0 };
    }
    return { enabled: this.stored.enabled, current, checking: this.inflight !== null, last };
  }

  setEnabled(enabled: unknown): UpdateState {
    if (typeof enabled === "boolean" && enabled !== this.stored.enabled) {
      this.stored = { ...this.stored, enabled };
      this.save();
    }
    return this.state();
  }

  isDue(): boolean {
    const last = this.stored.last;
    if (last === null) return true;
    const t = Date.parse(last.at);
    return !Number.isFinite(t) || this.now().getTime() - t >= CHECK_INTERVAL_MS || t > this.now().getTime();
  }

  async check(): Promise<UpdateState> {
    this.inflight ??= checkForUpdate({
      fetch: this.opts.fetch,
      current: this.opts.current,
      userAgent: this.opts.userAgent,
      now: this.now,
    }).then((result) => {
      this.stored = { ...this.stored, last: result };
      this.save();
      this.inflight = null;
      return result;
    });
    await this.inflight;
    return this.state();
  }

  /** Runs a check only if automatic checks are allowed, enabled, and one is due. */
  async maybeAutoCheck(): Promise<boolean> {
    if (!this.opts.autoCheck || !this.stored.enabled || this.inflight !== null || !this.isDue()) return false;
    await this.check();
    return true;
  }

  start(): void {
    if (!this.opts.autoCheck) return;
    const later = this.opts.setTimeout ?? setTimeout;
    const every = this.opts.setInterval ?? setInterval;
    later(() => void this.maybeAutoCheck(), START_DELAY_MS);
    every(() => void this.maybeAutoCheck(), WAKE_MS);
  }

  /** The release page to open, only when a newer release was found and its URL is allowlisted. */
  releaseUrl(): string | null {
    const last = this.state().last;
    if (last?.ok !== true || !last.updateAvailable || last.latest === null) return null;
    return isAllowedReleasePage(last.latest.url) ? last.latest.url : null;
  }
}
