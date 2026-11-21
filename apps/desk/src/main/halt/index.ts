import { join } from "node:path";
import { app } from "electron";
import type { IpcChannels } from "@shared/ipc";
import { loadSource } from "../data/source";
import { log } from "../log";
import { vps, vpsHandlers } from "../vps";
import { HaltManager, type CacheView } from "./manager";

/** `halt:*` IPC handlers (DESK-021). The flag lives in userData `halt.json` (off by default). */

/** Modes and halts come from the VPS cache: the server the command will run on. */
function vpsCache(): CacheView {
  const src = loadSource(vps().state().cacheDir);
  const portfolios = src.portfolios.map((p) => ({ name: p.name, mode: p.summary?.mode ?? null }));
  for (const s of src.status?.strategies ?? []) {
    if (!portfolios.some((p) => p.name === s.portfolio)) portfolios.push({ name: s.portfolio, mode: null });
  }
  return { portfolios, status: src.status };
}

let manager: HaltManager | null = null;

function halts(): HaltManager {
  manager ??= new HaltManager({
    storePath: join(app.getPath("userData"), "halt.json"),
    vpsConfig: () => vps().state().config,
    cache: vpsCache,
    // The same path as "Sync now", so the VPS source and its status.json refresh.
    sync: async () => (await vpsHandlers["vps:sync"]()).sync.ok,
    log,
  });
  return manager;
}

type HaltChannel = Extract<keyof IpcChannels, `halt:${string}`>;
type HaltHandlers = {
  [C in HaltChannel]: (
    request: IpcChannels[C]["request"],
  ) => IpcChannels[C]["response"] | Promise<IpcChannels[C]["response"]>;
};

export const haltHandlers: HaltHandlers = {
  "halt:get": () => halts().state(),
  "halt:setEnabled": (on) => halts().setEnabled(on),
  "halt:run": (req) => halts().run(req),
};
