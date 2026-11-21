import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron as electron, expect, test, type ElectronApplication, type Page } from "@playwright/test";

// DESK-021 halt/resume over SSH. Off by default: no controls. With the flag on, main's
// halt:get / halt:run handlers are replaced by fakes, so no ssh is ever spawned.
const appDir = join(import.meta.dirname, "..");
const mod = process.platform === "darwin" ? "Meta" : "Control";

let app: ElectronApplication;
let page: Page;

function launchEnv(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (v !== undefined && k !== "ELECTRON_RUN_AS_NODE") env[k] = v;
  }
  env["NEM_DESK_USER_DATA"] = mkdtempSync(join(tmpdir(), "nem-desk-e2e-"));
  return env;
}

test.beforeAll(async () => {
  app = await electron.launch({ args: [appDir], cwd: appDir, env: launchEnv() });
  page = await app.firstWindow();
});

test.afterAll(async () => {
  await app.close();
});

test("flag off: no halt controls, and main refuses halt:run", async () => {
  await page.getByRole("button", { name: "Load sample data" }).click();
  await page.keyboard.press(`${mod}+7`);
  await expect(page.getByRole("heading", { level: 1, name: "Feeds and Status", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Halts" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Halt controls" })).toHaveCount(0);

  const r = await page.evaluate(() =>
    window.nem?.sources.vps.halt?.run({ action: "halt", scope: "fixture", reason: "x", confirm: "fixture" }),
  );
  expect(r?.ok).toBe(false);
  expect(r !== undefined && !r.ok ? r.error.kind : null).toBe("disabled");

  await page.keyboard.press(`${mod}+8`);
  const box = page.getByLabel("Enable halt/resume over SSH (experimental)");
  await expect(box).not.toBeChecked();
});

test("flag on (mocked main): typed confirmation gates the halt, output is shown", async () => {
  const box = page.getByLabel("Enable halt/resume over SSH (experimental)");
  await box.click();
  await expect(box).toBeChecked();

  await app.evaluate(({ ipcMain }) => {
    const state = {
      enabled: true,
      target: "nem@nem.example.org",
      runsAs: "cd '/home/nem/not-enough-markets' && .venv/bin/nem",
      portfolios: [{ name: "fixture", mode: "paper" }],
      strategies: ["fixture/btc_edge"],
      halts: [{ scope: "fixture/eth_no_edge", reason: "fixture", since: "2026-10-02T18:30:00Z" }],
      running: false,
    };
    const runs: unknown[] = [];
    (globalThis as { __haltRuns?: unknown[] }).__haltRuns = runs;
    ipcMain.removeHandler("halt:get");
    ipcMain.removeHandler("halt:run");
    ipcMain.handle("halt:get", () => state);
    ipcMain.handle("halt:run", (_e, req: unknown) => {
      runs.push(req);
      return { ok: true, command: "ssh nem@nem.example.org (mocked)", output: "Halted fixture/btc_edge: no new entries.", synced: true, state };
    });
  });
  await page.reload();
  await expect(page.getByRole("navigation", { name: "Screens" })).toBeVisible();
  await page.keyboard.press(`${mod}+7`);
  await expect(page.getByRole("heading", { level: 1, name: "Feeds and Status", exact: true })).toBeVisible();
  const panel = page.getByRole("region", { name: "Halt controls" });
  await expect(panel).toBeVisible();
  const halt = panel.getByRole("group", { name: "Halt" });
  await halt.getByLabel("Scope").selectOption("fixture/btc_edge");
  await halt.getByLabel("Reason").fill("check fills");
  const go = halt.getByRole("button", { name: "Halt fixture/btc_edge" });
  await expect(go).toBeDisabled();
  await halt.getByLabel(/to confirm/).fill("fixture");
  await expect(go).toBeDisabled();
  await halt.getByLabel(/to confirm/).fill("fixture/btc_edge");
  await expect(go).toBeEnabled();
  await go.click();
  await expect(halt.getByTestId("halt-result")).toContainText("Halted fixture/btc_edge: no new entries.");
  const runs = await app.evaluate(() => (globalThis as { __haltRuns?: unknown[] }).__haltRuns ?? []);
  expect(runs).toEqual([{ action: "halt", scope: "fixture/btc_edge", reason: "check fills", confirm: "fixture/btc_edge" }]);

  await expect(panel.getByRole("group", { name: "Resume" }).getByRole("option")).toHaveText(["fixture/eth_no_edge"]);
});
