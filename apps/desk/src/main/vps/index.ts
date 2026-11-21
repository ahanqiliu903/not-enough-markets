import { homedir } from "node:os";
import { join } from "node:path";
import { app, BrowserWindow, dialog } from "electron";
import type { IpcChannels } from "@shared/ipc";
import { sources } from "../data/handlers";
import { VpsManager } from "./sync";

/** `vps:*` IPC handlers (DESK-013). Config and cache live in userData, never in the repo. */

export const VPS_SOURCE_ID = "vps";

let manager: VpsManager | null = null;

function vps(): VpsManager {
  manager ??= new VpsManager({
    storePath: join(app.getPath("userData"), "vps.json"),
    cacheDir: join(app.getPath("userData"), "vps-cache"),
    onSynced: (cacheDir, c) => {
      sources().upsertManaged({ id: VPS_SOURCE_ID, label: `VPS ${c.user}@${c.host}`, kind: "vps", path: cacheDir });
    },
  });
  return manager;
}

/** Loads the saved config so its auto-sync timer (if any) runs from app start. */
export function startVps(): void {
  vps();
}

type VpsChannel = Extract<keyof IpcChannels, `vps:${string}`>;
type VpsHandlers = {
  [C in VpsChannel]: (
    request: IpcChannels[C]["request"],
  ) => IpcChannels[C]["response"] | Promise<IpcChannels[C]["response"]>;
};

export const vpsHandlers: VpsHandlers = {
  "vps:get": () => vps().state(),
  "vps:save": (req) => vps().save(req),
  "vps:forget": () => vps().forget(),
  "vps:sync": async () => {
    const sync = await vps().sync();
    return { state: vps().state(), sync, sources: sources().state() };
  },
  "vps:pickKey": async () => {
    const win = BrowserWindow.getFocusedWindow();
    const opts = {
      title: "Choose your SSH private key (its contents are never read)",
      defaultPath: join(homedir(), ".ssh"),
      properties: ["openFile" as const, "showHiddenFiles" as const],
    };
    const picked = win !== null ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
    return picked.canceled ? null : (picked.filePaths[0] ?? null);
  },
};
