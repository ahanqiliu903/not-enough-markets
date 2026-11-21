import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron as electron, expect, test, type ElectronApplication, type Page } from "@playwright/test";

const appDir = join(import.meta.dirname, "..");
const pkg = JSON.parse(readFileSync(join(appDir, "package.json"), "utf8")) as { version: string };
const mod = process.platform === "darwin" ? "Meta" : "Control";

let app: ElectronApplication;
let page: Page;
const consoleErrors: string[] = [];

// Some hosts (editor extensions) export ELECTRON_RUN_AS_NODE=1, which makes Electron
// behave as plain Node and refuse to open a window.
function launchEnv(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (v !== undefined && k !== "ELECTRON_RUN_AS_NODE") env[k] = v;
  }
  // A fresh profile, so first-run state (no data source) doesn't depend on this machine.
  env["NEM_DESK_USER_DATA"] = mkdtempSync(join(tmpdir(), "nem-desk-e2e-"));
  return env;
}

test.beforeAll(async () => {
  app = await electron.launch({
    // Launch the app directory (not the script) so app.getVersion() reads package.json.
    args: [appDir],
    cwd: appDir,
    env: launchEnv(),
  });
  page = await app.firstWindow();
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(err.message));
});

test.afterAll(async () => {
  await app.close();
});

test("shows the shell", async () => {
  await expect(page.getByRole("navigation", { name: "Screens" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Portfolios", exact: true })).toBeVisible();
  await expect(page.getByRole("status", { name: "No portfolios yet" })).toBeVisible();
  await expect(page.getByText("make demo")).toBeVisible();
  await expect(page.getByTestId("status-version")).toContainText(`v${pkg.version}`);
});

test("renderer is isolated: no Node, only the allowlisted API", async () => {
  const probe = await page.evaluate(() => ({
    require: typeof (globalThis as { require?: unknown }).require,
    process: typeof (globalThis as { process?: unknown }).process,
    nem: Object.keys(window.nem ?? {}).sort(),
  }));
  expect(probe.require).toBe("undefined");
  expect(probe.process).toBe("undefined");
  expect(probe.nem).toEqual(["appVersion", "data", "kalshi", "listSources", "platform", "sources"]);
  expect(await page.evaluate(() => window.nem?.listSources())).toEqual([]);
});

test("switches screens with mod+2", async () => {
  await page.keyboard.press(`${mod}+2`);
  await expect(page.getByRole("heading", { level: 1, name: "Strategy", exact: true })).toBeVisible();
  await page.keyboard.press(`${mod}+1`);
  await expect(page.getByRole("heading", { level: 1, name: "Portfolios", exact: true })).toBeVisible();
});

test("opens the command palette and jumps to a screen", async () => {
  await page.keyboard.press(`${mod}+k`);
  const dialog = page.getByRole("dialog", { name: "Command palette" });
  await expect(dialog).toBeVisible();
  await page.keyboard.type("trades");
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("heading", { level: 1, name: "Trades", exact: true })).toBeVisible();
});

test("bundled fonts load (no CDN)", async () => {
  const loaded = await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([
      document.fonts.load('13px "JetBrains Mono"'),
      document.fonts.load('13px "Silkscreen"'),
    ]);
    return [document.fonts.check('13px "JetBrains Mono"'), document.fonts.check('13px "Silkscreen"')];
  });
  expect(loaded).toEqual([true, true]);
});

test("pixel accents off does not move layout", async () => {
  const boxes = () =>
    page.evaluate(() =>
      [".nav-pane", ".pane-title", ".main-pane", ".status-bar", ".command-bar"].map((sel) => {
        const r = document.querySelector(sel)?.getBoundingClientRect();
        return r ? [r.x, r.y, r.width, r.height] : null;
      }),
    );
  await page.keyboard.press(`${mod}+1`);
  await expect(page.getByRole("status", { name: "No portfolios yet" })).toBeVisible();
  const before = await boxes();
  if (process.env["NEM_SCREENSHOT_DIR"] !== undefined) {
    await page.screenshot({ path: join(process.env["NEM_SCREENSHOT_DIR"], "shell-pixel-on.png") });
  }
  await page.keyboard.press(`${mod}+k`);
  await page.keyboard.type("pixel accents");
  await page.keyboard.press("Enter");
  await expect(page.locator("html")).toHaveAttribute("data-pixel", "off");
  expect(await boxes()).toEqual(before);
  if (process.env["NEM_SCREENSHOT_DIR"] !== undefined) {
    await page.screenshot({ path: join(process.env["NEM_SCREENSHOT_DIR"], "shell-pixel-off.png") });
  }
  await page.keyboard.press(`${mod}+k`);
  await page.keyboard.type("pixel accents");
  await page.keyboard.press("Enter");
  await expect(page.locator("html")).toHaveAttribute("data-pixel", "on");
});

test("no console errors (including CSP violations)", () => {
  expect(consoleErrors).toEqual([]);
});
