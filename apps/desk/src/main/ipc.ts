import { app, ipcMain } from "electron";
import type { IpcChannel, IpcChannels, IpcRequest, IpcResponse } from "@shared/ipc";
import { IPC_CHANNELS } from "@shared/ipc";

type Handlers = {
  [C in IpcChannel]: (request: IpcRequest<C>) => IpcResponse<C> | Promise<IpcResponse<C>>;
};

/** One handler per channel; the mapped type makes a missing handler a compile error. */
export const handlers: Handlers = {
  "app:version": () => app.getVersion(),
  // Stub until the data layer (DESK-003) lands.
  "data:listSources": () => [],
};

export function registerIpc(isTrustedSender: (url: string) => boolean): void {
  for (const channel of IPC_CHANNELS) {
    ipcMain.handle(channel, (event, request: unknown) => {
      const url = event.senderFrame?.url ?? "";
      if (!isTrustedSender(url)) {
        throw new Error(`IPC from untrusted frame rejected: ${channel}`);
      }
      const handler = handlers[channel] as (r: unknown) => IpcChannels[typeof channel]["response"];
      return handler(request);
    });
  }
}
