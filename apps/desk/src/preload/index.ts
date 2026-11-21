import { contextBridge, ipcRenderer } from "electron";
import type { IpcChannel, IpcRequest, IpcResponse, NemApi } from "@shared/ipc";
import { DATA_CHANGED_EVENT, isIpcChannel } from "@shared/ipc";

function invoke<C extends IpcChannel>(channel: C, request?: IpcRequest<C>): Promise<IpcResponse<C>> {
  if (!isIpcChannel(channel)) {
    return Promise.reject(new Error(`IPC channel not allowed: ${String(channel)}`));
  }
  return ipcRenderer.invoke(channel, request) as Promise<IpcResponse<C>>;
}

const api: NemApi = {
  appVersion: () => invoke("app:version"),
  listSources: () => invoke("data:listSources"),
  data: {
    snapshot: () => invoke("data:snapshot"),
    trades: (query) => invoke("data:trades", query),
    tradesCsv: (filter) => invoke("data:tradesCsv", filter),
    curves: (keys) => invoke("data:curves", { keys }),
    onChanged: (callback) => {
      const listener = (_e: unknown, payload: { revision?: unknown }) => {
        if (typeof payload.revision === "number") callback(payload.revision);
      };
      ipcRenderer.on(DATA_CHANGED_EVENT, listener);
      return () => {
        ipcRenderer.removeListener(DATA_CHANGED_EVENT, listener);
      };
    },
    db: {
      info: () => invoke("db:info"),
      markets: (query) => invoke("db:markets", query),
      top: (ticker) => invoke("db:top", ticker),
      depth: (ticker, id) => invoke("db:depth", id !== undefined ? { ticker, id } : { ticker }),
      feed: (query) => invoke("db:feed", query),
    },
    updates: {
      get: () => invoke("updates:get"),
      check: () => invoke("updates:check"),
      setEnabled: (enabled) => invoke("updates:setEnabled", enabled),
      openRelease: () => invoke("updates:openRelease"),
    },
    diagnostics: {
      get: () => invoke("diag:get"),
      copy: () => invoke("diag:copy"),
      revealLog: () => invoke("diag:revealLog"),
    },
  },
  sources: {
    list: () => invoke("sources:list"),
    addFolder: (path) => invoke("sources:addFolder", path !== undefined ? { path } : {}),
    addSample: () => invoke("sources:addSample"),
    remove: (id) => invoke("sources:remove", { id }),
    setActive: (id) => invoke("sources:setActive", { id }),
    attachDb: (id, path) => invoke("db:attach", path !== undefined ? { id, path } : { id }),
    detachDb: (id) => invoke("db:detach", { id }),
    vps: {
      get: () => invoke("vps:get"),
      save: (config) => invoke("vps:save", config),
      forget: () => invoke("vps:forget"),
      sync: () => invoke("vps:sync"),
      pickKey: () => invoke("vps:pickKey"),
      halt: {
        get: () => invoke("halt:get"),
        setEnabled: (enabled) => invoke("halt:setEnabled", enabled),
        run: (request) => invoke("halt:run", request),
      },
    },
    sheets: {
      get: () => invoke("sheets:get"),
      save: (req) => invoke("sheets:save", req),
      forget: () => invoke("sheets:forget"),
      sync: () => invoke("sheets:sync"),
    },
  },
  kalshi: {
    searchSeries: (query) => invoke("kalshi:searchSeries", query),
    series: (ticker) => invoke("kalshi:series", ticker),
    markets: (series) => invoke("kalshi:markets", series),
    market: (ticker) => invoke("kalshi:market", ticker),
    orderbook: (ticker) => invoke("kalshi:orderbook", ticker),
    copyStrategyCommand: (series) => invoke("kalshi:copyStrategyCommand", series),
  },

  platform: process.platform,
};

contextBridge.exposeInMainWorld("nem", api);
