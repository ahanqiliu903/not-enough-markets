import type { Diagnostics } from "@shared/diagnostics";
import type { Redactor } from "../log/redact";

/**
 * DESK-020: the diagnostics report. `redactDeep` runs every string through the redactor,
 * so private values never reach the renderer or the clipboard; `diagnosticsText` is the
 * plain-text form "Copy diagnostics" puts on the clipboard.
 */

export function redactDeep<T>(value: T, r: Redactor): T {
  if (typeof value === "string") return r.redact(value) as T;
  if (Array.isArray(value)) return value.map((v: unknown) => redactDeep(v, r)) as T;
  if (typeof value === "object" && value !== null) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) out[k] = redactDeep(v, r);
    return out as T;
  }
  return value;
}

export function diagnosticsText(d: Diagnostics): string {
  const a = d.app;
  const lines = [
    "NEM Desk diagnostics (private values replaced with <placeholders>)",
    `generated: ${d.generatedAt}`,
    `app: ${a.version}${a.packaged ? "" : " (unpackaged)"}  electron ${a.electron}  chrome ${a.chrome}  node ${a.node}  ${a.platform}/${a.arch}`,
    `contract: ${d.contract.expected}  db user_version: ${d.contract.dbSchemaVersions.join(", ")}`,
    `userData: ${d.userData}`,
    `log: ${d.logFile}`,
    "",
    `sources (${d.sources.length}):`,
  ];
  for (const s of d.sources) {
    lines.push(
      `  ${s.active ? "*" : "-"} ${s.label} [${s.kind}] ${s.path}`,
      `      db: ${s.db ?? "none"}  last load: ${s.lastLoadAt ?? "not this session"}  problems: ${s.errorCount ?? "-"}`,
      ...s.errors.map((e) => `      ! ${e}`),
    );
  }
  lines.push("");
  if (d.db === null) lines.push("db: none");
  else if ("error" in d.db) lines.push(`db: ${d.db.error}`);
  else {
    lines.push(
      `db: ${d.db.path}  user_version ${d.db.schemaVersion}  ${d.db.sizeBytes} bytes  ${d.db.series} series  ${d.db.feeds} feeds  newest ${d.db.newestTs ?? "-"}`,
    );
  }
  const sh = d.sheets;
  lines.push(
    `google sheet: ${sh.configured ? `configured, portfolios ${sh.portfolios.join(", ")}, auto ${sh.intervalMinutes === null ? "off" : `${sh.intervalMinutes} min`}` : "not configured"}`,
    `  last sync: ${sh.lastSync ?? "-"}`,
    `vps: ${d.vps.configured ? `configured, auto ${d.vps.intervalMinutes === null ? "off" : `${d.vps.intervalMinutes} min`}` : "not configured"}`,
    `  last sync: ${d.vps.lastSync ?? "-"}`,
    "",
    `recent log (${d.recentLog.length} lines):`,
    ...d.recentLog,
  );
  return `${lines.join("\n")}\n`;
}
