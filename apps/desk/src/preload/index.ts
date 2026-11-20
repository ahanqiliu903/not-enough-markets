import { contextBridge, ipcRenderer } from "electron";
import type { IpcChannel, IpcRequest, IpcResponse, NemApi } from "@shared/ipc";
import { isIpcChannel } from "@shared/ipc";

function invoke<C extends IpcChannel>(channel: C, request?: IpcRequest<C>): Promise<IpcResponse<C>> {
  if (!isIpcChannel(channel)) {
    return Promise.reject(new Error(`IPC channel not allowed: ${String(channel)}`));
  }
  return ipcRenderer.invoke(channel, request) as Promise<IpcResponse<C>>;
}

const api: NemApi = {
  appVersion: () => invoke("app:version"),
  listSources: () => invoke("data:listSources"),
  platform: process.platform,
};

contextBridge.exposeInMainWorld("nem", api);
