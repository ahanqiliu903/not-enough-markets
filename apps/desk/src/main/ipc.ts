import { app, ipcMain } from "electron";
import type { IpcChannel, IpcChannels, IpcRequest, IpcResponse } from "@shared/ipc";
import { IPC_CHANNELS } from "@shared/ipc";
import { dataHandlers } from "./data/handlers";
import { dbHandlers } from "./db/handlers";
import { kalshiHandlers } from "./kalshi";
import { startVps, vpsHandlers } from "./vps";

type Handlers = {
  [C in IpcChannel]: (request: IpcRequest<C>) => IpcResponse<C> | Promise<IpcResponse<C>>;
};

/** One handler per channel; the mapped type makes a missing handler a compile error. */
export const handlers: Handlers = {
  "app:version": () => app.getVersion(),
  ...dataHandlers,
  "kalshi:searchSeries": (q) => kalshiHandlers.searchSeries(q),
  "kalshi:series": (t) => kalshiHandlers.series(t),
  "kalshi:markets": (s) => kalshiHandlers.markets(s),
  "kalshi:market": (t) => kalshiHandlers.market(t),
  "kalshi:orderbook": (t) => kalshiHandlers.orderbook(t),
  "kalshi:copyStrategyCommand": (s) => kalshiHandlers.copyStrategyCommand(s),
  ...dbHandlers,
  ...vpsHandlers,
};

export function registerIpc(isTrustedSender: (url: string) => boolean): void {
  startVps();
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
