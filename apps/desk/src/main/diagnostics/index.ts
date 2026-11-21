import { app, clipboard, shell } from "electron";
import { DB_SCHEMA_VERSIONS } from "@shared/db";
import { EXPECTED_VERSION } from "@shared/data";
import type { DiagCopyResult, Diagnostics } from "@shared/diagnostics";
import type { IpcChannels } from "@shared/ipc";
import { sources } from "../data/handlers";
import { dbHandlers } from "../db/handlers";
import { log } from "../log";
import { errorText } from "../log/logger";
import type { Secret } from "../log/redact";
import { sheets } from "../sheets";
import { vps } from "../vps";
import { diagnosticsText, redactDeep } from "./report";

/** `diag:*` IPC handlers (DESK-020). Everything leaves main redacted. */

let started = false;

/** Logs app start, hooks crash reporting, and registers the VPS values to redact. */
export function startDiagnostics(): void {
  if (started) return;
  started = true;
  const l = log();
  l.redactor.addProvider((): Secret[] => {
    const c = vps().state().config;
    if (c === null) return [];
    return [
      { value: c.keyPath, placeholder: "<key-path>" },
      { value: `${c.user}@${c.host}`, placeholder: "<user>@<host>" },
      { value: c.host, placeholder: "<host>" },
      { value: c.user, placeholder: "<user>" },
    ];
  });
  l.info("app", `start ${app.getVersion()} electron ${process.versions.electron} ${process.platform}/${process.arch}${app.isPackaged ? "" : " (unpackaged)"}`);
  process.on("unhandledRejection", (reason) => l.error("main", `unhandled rejection: ${errorText(reason)}`));
  app.on("render-process-gone", (_e, _wc, details) => l.error("renderer", `gone: ${details.reason} (exit ${details.exitCode})`));
  app.on("child-process-gone", (_e, details) => l.error("child", `${details.type} gone: ${details.reason}`));
}

export function collectDiagnostics(): Diagnostics {
  const sm = sources();
  const st = sm.state();
  const dbInfo = dbHandlers["db:info"](undefined);
  let db: Diagnostics["db"] = null;
  if (!(dbInfo instanceof Promise)) {
    if (dbInfo.ok) {
      const v = dbInfo.value;
      db = { path: v.path, schemaVersion: v.schemaVersion, sizeBytes: v.sizeBytes, series: v.series.length, feeds: v.feeds.length, newestTs: v.newestTs };
    } else if (dbInfo.error.kind !== "no_db") {
      db = { error: `${dbInfo.error.kind}: ${dbInfo.error.message}` };
    }
  }
  const sh = sheets().state();
  const sl = sh.lastSync;
  const vs = vps().state();
  const vl = vs.lastSync;
  const raw: Diagnostics = {
    generatedAt: new Date().toISOString(),
    app: {
      version: app.getVersion(),
      electron: process.versions.electron,
      chrome: process.versions.chrome,
      node: process.versions.node,
      platform: process.platform,
      arch: process.arch,
      packaged: app.isPackaged,
    },
    contract: { expected: EXPECTED_VERSION, dbSchemaVersions: [...DB_SCHEMA_VERSIONS] },
    userData: app.getPath("userData"),
    logFile: log().file,
    sources: st.sources.map((s) => {
      const load = sm.loadInfo(s.id);
      return {
        id: s.id,
        label: s.label,
        kind: s.kind,
        path: s.path,
        active: s.id === st.activeId,
        db: st.dbPaths?.[s.id] ?? null,
        lastLoadAt: load?.at ?? null,
        errorCount: load?.errorCount ?? null,
        errors: load?.errors ?? [],
      };
    }),
    db,
    sheets: {
      configured: sh.configured,
      portfolios: sh.portfolios,
      intervalMinutes: sh.intervalMinutes,
      lastSync: sl === null ? null : `${sl.at} ${sl.ok ? "ok" : `failed (${sl.error?.kind ?? "?"}: ${sl.error?.message ?? ""})`}`,
    },
    vps: {
      configured: vs.config !== null,
      intervalMinutes: vs.config?.intervalMinutes ?? null,
      lastSync: vl === null ? null : `${vl.at} ${vl.ok ? "ok" : `failed (${vl.error?.kind ?? "?"}: ${vl.error?.message ?? ""})`}`,
    },
    recentLog: log().recent(100),
  };
  return redactDeep(raw, log().redactor);
}

type DiagChannel = Extract<keyof IpcChannels, `diag:${string}`>;
type DiagHandlers = {
  [C in DiagChannel]: (
    request: IpcChannels[C]["request"],
  ) => IpcChannels[C]["response"] | Promise<IpcChannels[C]["response"]>;
};

export const diagHandlers: DiagHandlers = {
  "diag:get": () => collectDiagnostics(),
  "diag:copy": async (): Promise<DiagCopyResult> => {
    // Built from the redacted report, then redacted once more as a whole.
    const text = log().redactor.redact(diagnosticsText(collectDiagnostics()));
    try {
      await clipboard.writeText(text);
    } catch (e) {
      return { ok: false, message: `clipboard: ${errorText(e)}` };
    }
    log().info("diag", "diagnostics copied to the clipboard");
    return { ok: true, chars: text.length };
  },
  "diag:revealLog": () => {
    log().info("diag", "reveal log file");
    shell.showItemInFolder(log().file);
    return true;
  },
};
