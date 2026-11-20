/**
 * The single source of truth for renderer -> main IPC.
 *
 * Every channel the renderer may call is listed in `IpcChannels` with its request and
 * response types. The preload exposes only these channels (see `IPC_CHANNELS`), and the
 * main process registers a handler for each one (see `src/main/ipc.ts`). All channels are
 * read-only: the desk app never writes to the engine, Kalshi, or the user's files.
 *
 * To add a channel: add an entry here, add its name to `IPC_CHANNELS`, implement the
 * handler in `src/main/ipc.ts`, and add a method to `NemApi` + `src/preload/index.ts`.
 */

/** A place the desk app can read engine output from (filled in by DESK-003). */
export interface DataSource {
  id: string;
  label: string;
  kind: "sqlite" | "csv" | "json";
  path: string;
}

export interface IpcChannels {
  "app:version": { request: void; response: string };
  "data:listSources": { request: void; response: DataSource[] };
}

export type IpcChannel = keyof IpcChannels;
export type IpcRequest<C extends IpcChannel> = IpcChannels[C]["request"];
export type IpcResponse<C extends IpcChannel> = IpcChannels[C]["response"];

export const IPC_CHANNELS = ["app:version", "data:listSources"] as const satisfies readonly IpcChannel[];

// Compile-time check that IPC_CHANNELS lists every channel in IpcChannels.
type Missing = Exclude<IpcChannel, (typeof IPC_CHANNELS)[number]>;
type AssertNever<T extends never> = T;
export type IpcChannelsComplete = AssertNever<Missing>;

export function isIpcChannel(value: unknown): value is IpcChannel {
  return typeof value === "string" && (IPC_CHANNELS as readonly string[]).includes(value);
}

/** The API the preload puts on `window.nem`. */
export interface NemApi {
  appVersion: () => Promise<string>;
  listSources: () => Promise<DataSource[]>;
  /** Platform string, used for Cmd vs Ctrl labels. */
  platform: string;
}
