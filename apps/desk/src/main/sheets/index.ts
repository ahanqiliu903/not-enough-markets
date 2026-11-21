import { join } from "node:path";
import { app } from "electron";
import type { IpcChannels } from "@shared/ipc";
import { sources } from "../data/handlers";
import { log } from "../log";
import { SheetsClient, type SheetsFetch } from "./client";
import { fixtureSheetsFetch } from "./fixtures";
import { SheetsManager } from "./sync";

/** `sheets:*` IPC handlers (DESK-012). Config and cache live in userData, never in the repo. */

export const SHEETS_SOURCE_ID = "sheets";
export const SHEETS_FIXTURES_ENV = "NEM_DESK_SHEETS_FIXTURES";

let manager: SheetsManager | null = null;

export function sheets(): SheetsManager {
  if (manager !== null) return manager;
  const fixtures = process.env[SHEETS_FIXTURES_ENV];
  // Recorded fixtures only in dev/test builds; a packaged app always talks to Google.
  const fetchFn: SheetsFetch =
    fixtures !== undefined && fixtures !== "" && !app.isPackaged ? fixtureSheetsFetch(fixtures) : (url, init) => fetch(url, init);
  const m = new SheetsManager({
    storePath: join(app.getPath("userData"), "sheets.json"),
    cacheDir: join(app.getPath("userData"), "sheets-cache"),
    client: new SheetsClient({ fetch: fetchFn, userAgent: `NEM Desk/${app.getVersion()}` }),
    log: (level, message) => log()[level]("sheets", message),
    onSynced: (cacheDir, reason) => {
      const s = sources();
      const exists = s.state().sources.some((x) => x.id === SHEETS_SOURCE_ID);
      // A background poll refreshes the source but never takes over the active one.
      if (reason === "poll" && exists) s.checkForChanges();
      else s.upsertManaged({ id: SHEETS_SOURCE_ID, label: "Google Sheet", kind: "sheets", path: cacheDir });
    },
    onForget: () => {
      sources().remove(SHEETS_SOURCE_ID);
    },
  });
  manager = m;
  log().redactor.addProvider(() => {
    const id = m.sheetId();
    return id === null ? [] : [{ value: id, placeholder: "<sheet-id>" }];
  });
  return m;
}

/** Loads the saved sheet so its poll timer (if any) runs from app start. */
export function startSheets(): void {
  sheets();
}

type SheetsChannel = Extract<keyof IpcChannels, `sheets:${string}`>;
type SheetsHandlers = {
  [C in SheetsChannel]: (
    request: IpcChannels[C]["request"],
  ) => IpcChannels[C]["response"] | Promise<IpcChannels[C]["response"]>;
};

export const sheetsHandlers: SheetsHandlers = {
  "sheets:get": () => sheets().state(),
  "sheets:save": (req) => sheets().save(req),
  "sheets:forget": () => sheets().forget(),
  "sheets:sync": async () => {
    const sync = await sheets().sync("manual");
    return { state: sheets().state(), sync, sources: sources().state() };
  },
};
