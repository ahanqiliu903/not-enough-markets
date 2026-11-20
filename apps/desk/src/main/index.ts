import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { app, BrowserWindow, session, shell } from "electron";
import { colors } from "../renderer/theme";
import { registerIpc } from "./ipc";

const devServerUrl = process.env["ELECTRON_RENDERER_URL"];

// e2e tests run against a throwaway profile (source list etc.); never in a packaged build.
const userDataOverride = process.env["NEM_DESK_USER_DATA"];
if (userDataOverride !== undefined && userDataOverride !== "" && !app.isPackaged) {
  app.setPath("userData", userDataOverride);
}
const rendererFile = join(import.meta.dirname, "../renderer/index.html");
const rendererFileUrl = pathToFileURL(rendererFile).href;

/** Only our own bundled page (or the local Vite dev server in dev) may talk to main. */
function isTrustedUrl(url: string): boolean {
  if (devServerUrl !== undefined && !app.isPackaged && url.startsWith(devServerUrl)) {
    return true;
  }
  return url.split("#")[0]?.split("?")[0] === rendererFileUrl;
}

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 560,
    show: false,
    // Matches the --bg token so there is no white flash before the renderer paints.
    backgroundColor: colors.bg,
    title: "NEM Desk",
    webPreferences: {
      preload: join(import.meta.dirname, "../preload/index.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      spellcheck: false,
    },
  });

  win.once("ready-to-show", () => win.show());

  // No remote content: block every navigation and popup away from our own page.
  win.webContents.on("will-navigate", (event, url) => {
    if (!isTrustedUrl(url)) event.preventDefault();
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    // Only plain https links go to the user's browser; nothing opens inside the app.
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });

  if (devServerUrl !== undefined && !app.isPackaged) {
    void win.loadURL(devServerUrl);
  } else {
    void win.loadFile(rendererFile);
  }
  return win;
}

app.on("web-contents-created", (_event, contents) => {
  contents.on("will-attach-webview", (event) => event.preventDefault());
});

void app.whenReady().then(() => {
  // The desk app needs no camera, mic, notifications, geolocation, etc.
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);

  registerIpc(isTrustedUrl);
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
