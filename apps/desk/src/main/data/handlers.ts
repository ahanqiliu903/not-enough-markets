import { join, resolve } from "node:path";
import { app, BrowserWindow, dialog } from "electron";
import {
  TRADE_COLUMNS,
  type CurveKey,
  type SourceResult,
  type TradeColumn,
  type TradeResult,
  type TradesFilter,
  type TradesQuery,
} from "@shared/data";
import { DATA_CHANGED_EVENT, type IpcChannels } from "@shared/ipc";
import { SourceManager } from "./sources";

/**
 * IPC handlers for the `data:*` and `sources:*` channels. Requests come from the renderer,
 * so every field is checked here before it reaches the data layer.
 */


/**
 * Sample data: in dev the repo's contract/fixtures/basic; in a packaged build a copy
 * bundled as an extraResource (`sample/`, see electron-builder.yml).
 */
export function samplePath(): string {
  return app.isPackaged
    ? join(process.resourcesPath, "sample")
    : resolve(app.getAppPath(), "../../contract/fixtures/basic");
}

let manager: SourceManager | null = null;

export function sources(): SourceManager {
  manager ??= new SourceManager({
    storePath: join(app.getPath("userData"), "sources.json"),
    samplePath: samplePath(),
    onChange: (revision) => {
      for (const w of BrowserWindow.getAllWindows()) {
        if (!w.isDestroyed()) w.webContents.send(DATA_CHANGED_EVENT, { revision });
      }
    },
  });
  return manager;
}

function str(v: unknown, max = 512): string | undefined {
  return typeof v === "string" && v.length > 0 && v.length <= max ? v : undefined;
}

const RESULTS: readonly TradeResult[] = ["won", "lost", "open"];

export function parseFilter(req: unknown): TradesFilter {
  const r = (typeof req === "object" && req !== null ? req : {}) as Record<string, unknown>;
  const f: TradesFilter = {};
  const portfolio = str(r["portfolio"]);
  const strategy = str(r["strategy"]);
  const text = str(r["text"], 200);
  if (portfolio !== undefined) f.portfolio = portfolio;
  if (strategy !== undefined) f.strategy = strategy;
  if (text !== undefined) f.text = text;
  if (RESULTS.includes(r["result"] as TradeResult)) f.result = r["result"] as TradeResult;
  if (r["hideExcluded"] === true) f.hideExcluded = true;
  return f;
}

export function parseQuery(req: unknown): TradesQuery {
  const r = (typeof req === "object" && req !== null ? req : {}) as Record<string, unknown>;
  const int = (v: unknown, lo: number, hi: number, d: number): number =>
    typeof v === "number" && Number.isInteger(v) ? Math.min(hi, Math.max(lo, v)) : d;
  const q: TradesQuery = { ...parseFilter(req), offset: int(r["offset"], 0, 1e9, 0), limit: int(r["limit"], 1, 2000, 200) };
  const sort = r["sort"] as { column?: unknown; dir?: unknown } | undefined;
  if (
    typeof sort === "object" &&
    (TRADE_COLUMNS as readonly unknown[]).includes(sort.column) &&
    (sort.dir === "asc" || sort.dir === "desc")
  ) {
    q.sort = { column: sort.column as TradeColumn, dir: sort.dir };
  }
  return q;
}

function parseKeys(req: unknown): CurveKey[] {
  const keys = (req as { keys?: unknown } | null)?.keys;
  if (!Array.isArray(keys)) return [];
  return keys
    .slice(0, 8)
    .map((k: unknown) => {
      const o = (typeof k === "object" && k !== null ? k : {}) as Record<string, unknown>;
      const portfolio = str(o["portfolio"]);
      const strategy = str(o["strategy"]);
      return portfolio !== undefined && strategy !== undefined ? { portfolio, strategy } : null;
    })
    .filter((k): k is CurveKey => k !== null);
}

type DataChannel = Extract<keyof IpcChannels, `data:${string}` | `sources:${string}`>;

type DataHandlers = {
  [C in DataChannel]: (
    request: IpcChannels[C]["request"],
  ) => IpcChannels[C]["response"] | Promise<IpcChannels[C]["response"]>;
};

export const dataHandlers: DataHandlers = {
  "data:listSources": () => sources().state().sources,
  "data:snapshot": () => sources().snapshot(),
  "data:trades": (req) => sources().trades(parseQuery(req)),
  "data:tradesCsv": (req) => sources().tradesCsv(parseQuery(req)),
  "data:curves": (req) => sources().curves(parseKeys(req)),
  "data:equity": (req) => sources().equity(str((req as { portfolio?: unknown } | null)?.portfolio)),
  "sources:list": () => sources().state(),
  "sources:addFolder": async (req): Promise<SourceResult> => {
    const given = (req as { path?: unknown } | null)?.path;
    if (given !== undefined) return sources().add(given);
    const win = BrowserWindow.getFocusedWindow();
    const opts = {
      title: "Choose an engine output folder (out/ or its parent)",
      properties: ["openDirectory" as const],
    };
    const picked = win !== null ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
    const path = picked.filePaths[0];
    if (picked.canceled || path === undefined) {
      return { ok: false, state: sources().state(), message: "cancelled" };
    }
    return sources().add(path);
  },
  "sources:addSample": () => sources().addSample(),
  "sources:remove": (req) => sources().remove((req as { id?: unknown } | null)?.id),
  "sources:setActive": (req) => sources().setActive((req as { id?: unknown } | null)?.id),
};
