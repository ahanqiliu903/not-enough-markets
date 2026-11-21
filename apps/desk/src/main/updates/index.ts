/** `updates:*` IPC handlers (DESK-019). State lives in userData `updates.json`. */
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { app, shell } from "electron";
import type { IpcChannels } from "@shared/ipc";
import { isAllowedReleasePage, type FetchLike } from "./check";
import { UpdateManager } from "./manager";

/** Unpackaged builds only: answer the releases request from this JSON file (e2e). */
export const UPDATE_FIXTURE_ENV = "NEM_DESK_UPDATE_FIXTURE";

function fixtureFetch(file: string): FetchLike {
  return async () => {
    const body: unknown = JSON.parse(await readFile(file, "utf8"));
    return { status: 200, json: () => Promise.resolve(body) };
  };
}

let manager: UpdateManager | null = null;

function updates(): UpdateManager {
  if (manager !== null) return manager;
  const fixture = process.env[UPDATE_FIXTURE_ENV];
  const useFixture = fixture !== undefined && fixture !== "" && !app.isPackaged;
  manager = new UpdateManager({
    storePath: join(app.getPath("userData"), "updates.json"),
    current: app.getVersion(),
    fetch: useFixture ? fixtureFetch(fixture) : (url, init) => fetch(url, init),
    userAgent: `NEM Desk/${app.getVersion()}`,
    // Dev and test runs never check on their own, so tests make no network requests.
    autoCheck: app.isPackaged,
  });
  return manager;
}

export function startUpdates(): void {
  updates().start();
}

type UpdatesChannel = Extract<keyof IpcChannels, `updates:${string}`>;
type UpdatesHandlers = {
  [C in UpdatesChannel]: (
    request: IpcChannels[C]["request"],
  ) => IpcChannels[C]["response"] | Promise<IpcChannels[C]["response"]>;
};

export const updatesHandlers: UpdatesHandlers = {
  "updates:get": () => updates().state(),
  "updates:check": () => updates().check(),
  "updates:setEnabled": (enabled) => updates().setEnabled(enabled),
  "updates:openRelease": async () => {
    // Main picks the URL (never the renderer) and re-checks the allowlist before opening.
    const url = updates().releaseUrl();
    if (url === null || !isAllowedReleasePage(url)) return false;
    try {
      await shell.openExternal(url);
      return true;
    } catch {
      return false;
    }
  },
};
