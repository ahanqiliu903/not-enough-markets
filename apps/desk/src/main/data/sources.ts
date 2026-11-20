import { randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, statSync, watch, writeFileSync, type FSWatcher } from "node:fs";
import { basename, dirname, isAbsolute, resolve } from "node:path";
import type {
  CurveKey,
  CurvesResult,
  DataSource,
  Snapshot,
  SnapshotResult,
  SourceResult,
  SourcesState,
  TradesCsv,
  TradesFilter,
  TradesPage,
  TradesQuery,
} from "@shared/data";
import { fingerprint, loadSource, type LoadedSource } from "./source";
import { pnlCurves, TradeIndex, tradesCsv } from "./trades";

/**
 * Data sources (DESK-004): the user's list of folders, persisted as JSON in the app's
 * userData folder (the desk's own settings, never the engine's files), which one is
 * active, and watching it. Each request reuses the last load unless a file changed
 * (mtime/size fingerprint), so a changed CSV is picked up on the next request even if a
 * watch event was missed. `onChange` fires within ~1.5 s of a change.
 */

export const SAMPLE_ID = "sample";
const POLL_MS = 1000;
const DEBOUNCE_MS = 150;

interface Stored {
  version: 1;
  sources: DataSource[];
  activeId: string | null;
}

export interface SourceManagerOptions {
  /** JSON file holding the source list. */
  storePath: string;
  /** Bundled sample data folder, or null when this build has none. */
  samplePath: string | null;
  onChange?: (revision: number) => void;
  /** Turn off watching + polling (tests drive `checkForChanges` directly). */
  watch?: boolean;
}

function isDir(p: string): boolean {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
}

function validSource(v: unknown): v is DataSource {
  if (typeof v !== "object" || v === null) return false;
  const s = v as Record<string, unknown>;
  return (
    typeof s["id"] === "string" &&
    typeof s["label"] === "string" &&
    typeof s["path"] === "string" &&
    (s["kind"] === "folder" || s["kind"] === "sample")
  );
}

export class SourceManager {
  private sources: DataSource[] = [];
  private activeId: string | null = null;
  private loaded: { id: string; fp: string; source: LoadedSource } | null = null;
  private revision = 0;
  private readonly index = new TradeIndex();
  private watcher: FSWatcher | null = null;
  private poll: ReturnType<typeof setInterval> | null = null;
  private debounce: ReturnType<typeof setTimeout> | null = null;
  private lastFp = "";

  constructor(private readonly opts: SourceManagerOptions) {
    this.read();
    this.rewatch();
  }

  private read(): void {
    try {
      const raw = JSON.parse(readFileSync(this.opts.storePath, "utf8")) as Partial<Stored>;
      this.sources = Array.isArray(raw.sources) ? raw.sources.filter(validSource) : [];
      const active = raw.activeId;
      this.activeId = typeof active === "string" && this.sources.some((s) => s.id === active) ? active : null;
    } catch {
      // First run or an unreadable file: start with no sources.
      this.sources = [];
      this.activeId = null;
    }
  }

  private write(): void {
    const data: Stored = { version: 1, sources: this.sources, activeId: this.activeId };
    try {
      mkdirSync(dirname(this.opts.storePath), { recursive: true });
      const tmp = `${this.opts.storePath}.tmp`;
      writeFileSync(tmp, JSON.stringify(data, null, 2));
      renameSync(tmp, this.opts.storePath);
    } catch {
      // Not persisting the list is survivable; the session still works.
    }
  }

  state(): SourcesState {
    return {
      sources: [...this.sources],
      activeId: this.activeId,
      sampleAvailable: this.opts.samplePath !== null && isDir(this.opts.samplePath),
    };
  }

  active(): DataSource | null {
    return this.sources.find((s) => s.id === this.activeId) ?? null;
  }

  add(path: unknown, label?: unknown): SourceResult {
    if (typeof path !== "string" || path.length === 0 || path.length > 4096 || !isAbsolute(path)) {
      return { ok: false, state: this.state(), message: "Pick an absolute folder path." };
    }
    const abs = resolve(path);
    if (!isDir(abs)) return { ok: false, state: this.state(), message: `Folder not found: ${abs}` };
    let source = this.sources.find((s) => s.path === abs);
    if (source === undefined) {
      const name = typeof label === "string" && label.trim() !== "" ? label.trim().slice(0, 80) : basename(abs);
      source = { id: randomUUID(), label: name, kind: "folder", path: abs };
      this.sources.push(source);
    }
    return this.activate(source);
  }

  addSample(): SourceResult {
    const p = this.opts.samplePath;
    if (p === null || !isDir(p)) {
      return { ok: false, state: this.state(), message: "This build has no bundled sample data." };
    }
    let source = this.sources.find((s) => s.id === SAMPLE_ID);
    if (source === undefined) {
      source = { id: SAMPLE_ID, label: "Sample data", kind: "sample", path: p };
      this.sources.push(source);
    } else {
      source.path = p;
    }
    return this.activate(source);
  }

  private activate(source: DataSource): SourceResult {
    this.activeId = source.id;
    this.write();
    this.rewatch();
    this.bump();
    return { ok: true, state: this.state(), source };
  }

  remove(id: unknown): SourcesState {
    if (typeof id !== "string") return this.state();
    this.sources = this.sources.filter((s) => s.id !== id);
    if (this.activeId === id) {
      this.activeId = this.sources[0]?.id ?? null;
      this.rewatch();
      this.bump();
    }
    this.write();
    return this.state();
  }

  setActive(id: unknown): SourcesState {
    const s = this.sources.find((x) => x.id === id);
    if (s !== undefined && s.id !== this.activeId) this.activate(s);
    return this.state();
  }

  private bump(): void {
    this.revision++;
    this.loaded = null;
    this.opts.onChange?.(this.revision);
  }

  /** Re-fingerprints the active source; bumps the revision if anything changed. */
  checkForChanges(): boolean {
    const s = this.active();
    if (s === null) return false;
    const fp = fingerprint(s.path);
    if (fp === this.lastFp) return false;
    this.lastFp = fp;
    this.bump();
    return true;
  }

  private rewatch(): void {
    this.watcher?.close();
    this.watcher = null;
    const s = this.active();
    this.lastFp = s === null ? "" : fingerprint(s.path);
    if (this.opts.watch === false) return;
    if (this.poll === null) {
      this.poll = setInterval(() => this.checkForChanges(), POLL_MS);
      this.poll.unref();
    }
    if (s === null || !isDir(s.path)) return;
    try {
      this.watcher = watch(s.path, { recursive: true, persistent: false }, () => {
        if (this.debounce !== null) clearTimeout(this.debounce);
        this.debounce = setTimeout(() => this.checkForChanges(), DEBOUNCE_MS);
      });
      this.watcher.on("error", () => {
        // e.g. the folder was deleted; polling keeps going and reports it.
        this.watcher?.close();
        this.watcher = null;
      });
    } catch {
      // Recursive watch unsupported here: the poll still catches changes.
    }
  }

  private load(): { source: DataSource; loaded: LoadedSource } | null {
    const s = this.active();
    if (s === null) return null;
    const fp = fingerprint(s.path);
    if (fp !== this.lastFp) {
      // Changed before the poll/watch noticed: count it now so every path agrees.
      this.lastFp = fp;
      this.revision++;
      this.opts.onChange?.(this.revision);
    }
    if (this.loaded?.id !== s.id || this.loaded.fp !== fp) {
      this.loaded = { id: s.id, fp, source: loadSource(s.path) };
    }
    return { source: s, loaded: this.loaded.source };
  }

  snapshot(): SnapshotResult {
    const l = this.load();
    if (l === null) return { state: "none" };
    const { source, loaded } = l;
    if (loaded.errors.some((e) => e.kind === "source")) {
      return { state: "error", source, errors: loaded.errors };
    }
    const snapshot: Snapshot = {
      source,
      outDir: loaded.outDir,
      loadedAt: new Date().toISOString(),
      revision: this.revision,
      portfolios: loaded.portfolios.map(({ name, summary, strategies, tradeCount, errors }) => ({
        name,
        summary,
        strategies,
        tradeCount,
        errors,
      })),
      extras: loaded.extras,
      hasSummaryJson: loaded.hasSummaryJson,
      status: loaded.status,
      backtests: loaded.backtests,
      errors: loaded.errors,
      newestAsOf: loaded.newestAsOf,
    };
    return { state: "ok", snapshot };
  }

  trades(q: TradesQuery): TradesPage {
    const l = this.load();
    if (l === null) return { ok: true, revision: this.revision, total: 0, offset: 0, rows: [] };
    const r = this.index.page(l.loaded, this.revision, q);
    if (r.errors.length > 0) return { ok: false, errors: r.errors };
    return { ok: true, revision: this.revision, total: r.total, offset: q.offset, rows: r.rows };
  }

  tradesCsv(f: TradesQuery | TradesFilter): TradesCsv {
    const l = this.load();
    if (l === null) return { ok: true, csv: "", rows: 0 };
    const { refs, errors } = this.index.query(l.loaded, this.revision, f);
    if (errors.length > 0) return { ok: false, errors };
    return { ok: true, csv: tradesCsv(refs), rows: refs.length };
  }

  curves(keys: CurveKey[]): CurvesResult {
    const l = this.load();
    if (l === null) return { ok: true, curves: [] };
    const r = pnlCurves(l.loaded, keys);
    if (r.errors.length > 0 && r.curves.length === 0) return { ok: false, errors: r.errors };
    return { ok: true, curves: r.curves };
  }

  dispose(): void {
    this.watcher?.close();
    if (this.poll !== null) clearInterval(this.poll);
    if (this.debounce !== null) clearTimeout(this.debounce);
    this.watcher = null;
    this.poll = null;
  }
}
