import { appendFileSync, mkdirSync, renameSync, rmSync, statSync } from "node:fs";
import { dirname } from "node:path";
import { Redactor } from "./redact";

/**
 * Main-process log (DESK-020): one line per event in `<userData>/logs/main.log`, rotated at
 * `maxBytes` into `main.log.1` .. `main.log.<keep>`. Every line is redacted before it is
 * written or kept in memory. Logging never throws.
 */

export type Level = "info" | "warn" | "error";

export interface LoggerOptions {
  file: string;
  maxBytes?: number;
  keep?: number;
  /** Lines kept in memory for the Diagnostics view. */
  recent?: number;
  now?: () => Date;
  redactor?: Redactor;
}

export const LOG_MAX_BYTES = 1024 * 1024;
export const LOG_KEEP = 3;

export class Logger {
  readonly file: string;
  readonly redactor: Redactor;
  private readonly maxBytes: number;
  private readonly keep: number;
  private readonly recentMax: number;
  private readonly now: () => Date;
  private readonly lines: string[] = [];
  private size = -1;

  constructor(opts: LoggerOptions) {
    this.file = opts.file;
    this.maxBytes = opts.maxBytes ?? LOG_MAX_BYTES;
    this.keep = opts.keep ?? LOG_KEEP;
    this.recentMax = opts.recent ?? 200;
    this.now = opts.now ?? (() => new Date());
    this.redactor = opts.redactor ?? new Redactor();
  }

  info(scope: string, message: string): void {
    this.write("info", scope, message);
  }
  warn(scope: string, message: string): void {
    this.write("warn", scope, message);
  }
  error(scope: string, message: string): void {
    this.write("error", scope, message);
  }

  recent(n = this.recentMax): string[] {
    return this.lines.slice(-n);
  }

  private rotate(): void {
    try {
      rmSync(`${this.file}.${this.keep}`, { force: true });
      for (let i = this.keep - 1; i >= 1; i--) {
        try {
          renameSync(`${this.file}.${i}`, `${this.file}.${i + 1}`);
        } catch {
          // that generation doesn't exist yet
        }
      }
      renameSync(this.file, `${this.file}.1`);
    } catch {
      // nothing to rotate
    }
    this.size = 0;
  }

  private write(level: Level, scope: string, message: string): void {
    const text = this.redactor.redact(message.replace(/\s*\n\s*/g, " | ")).slice(0, 2000);
    const line = `${this.now().toISOString()} ${level.toUpperCase().padEnd(5)} [${scope}] ${text}`;
    this.lines.push(line);
    if (this.lines.length > this.recentMax) this.lines.splice(0, this.lines.length - this.recentMax);
    try {
      if (this.size < 0) {
        mkdirSync(dirname(this.file), { recursive: true });
        try {
          this.size = statSync(this.file).size;
        } catch {
          this.size = 0;
        }
      }
      const bytes = Buffer.byteLength(line) + 1;
      if (this.size > 0 && this.size + bytes > this.maxBytes) this.rotate();
      appendFileSync(this.file, `${line}\n`, { mode: 0o600 });
      this.size += bytes;
    } catch {
      // A read-only or full disk must not take the app down; the in-memory lines remain.
    }
  }
}

export function errorText(e: unknown): string {
  return e instanceof Error ? `${e.name}: ${e.message}` : String(e);
}
