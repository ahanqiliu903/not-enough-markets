import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron as electron, expect, test, type ElectronApplication, type Page } from "@playwright/test";

/** DESK-014..017 end to end on "Load sample data" (contract/fixtures/basic). */

const appDir = join(import.meta.dirname, "..");
const mod = process.platform === "darwin" ? "Meta" : "Control";
const work = mkdtempSync(join(tmpdir(), "nem-desk-bt-e2e-"));

let app: ElectronApplication;
let page: Page;
const consoleErrors: string[] = [];

function launchEnv(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (v !== undefined && k !== "ELECTRON_RUN_AS_NODE") env[k] = v;
  }
  env["NEM_DESK_USER_DATA"] = join(work, "profile");
  return env;
}

test.beforeAll(async () => {
  app = await electron.launch({ args: [appDir], cwd: appDir, env: launchEnv() });
  page = await app.firstWindow();
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(err.message));
});

test.afterAll(async () => {
  await app.close();
  rmSync(work, { recursive: true, force: true });
});

test("Backtests is hidden until sample data loads, then lists the fixture run", async () => {
  await expect(page.getByRole("button", { name: "Load sample data" })).toBeVisible();
  await expect(page.getByRole("navigation").getByText("Backtests")).toHaveCount(0);
  await page.getByRole("button", { name: "Load sample data" }).click();
  await expect(page.getByRole("region", { name: "Portfolio fixture" })).toBeVisible();

  await page.keyboard.press(`${mod}+6`);
  await expect(page.getByRole("heading", { level: 1, name: "Backtests", exact: true })).toBeVisible();
  const runs = page.getByRole("table", { name: "Backtest runs" });
  await expect(runs.getByRole("row").nth(1)).toContainText("sol_undecided");
  await expect(runs.getByRole("row").nth(1)).toContainText("KXSOL15M");
  await expect(runs.getByRole("row").nth(1)).toContainText("SUGGESTED");
});

test("run detail: grid, windows, heatmap with chosen cell, train vs test", async () => {
  await page.getByRole("table", { name: "Backtest runs" }).getByText("sol_undecided", { exact: true }).click();
  await expect(page.getByRole("region", { name: "Chosen variant" })).toContainText("signal.threshold=0.86");
  await expect(page.getByRole("region", { name: "Chosen variant" })).toContainText("not estimated");
  await expect(page.getByRole("table", { name: "Windows" })).toContainText("2026-10-01 00:00:00Z");
  const hm = page.getByRole("table", { name: "Sweep heatmap" });
  await expect(hm.locator("td.heat-cell")).toHaveCount(8);
  await expect(hm.locator("td.heat-chosen")).toHaveCount(1);
  await expect(hm.locator("td.heat-chosen")).toContainText("n 119");
  await expect(page.getByRole("table", { name: "Train vs test metrics" })).toContainText("94.12%");
  await expect(page.getByRole("region", { name: "Train vs test" })).toContainText("chosen variant only, by design");
});

test("backtest vs paper vs live", async () => {
  const panel = page.getByRole("region", { name: "Backtest vs paper vs live" });
  const t = panel.getByRole("table", { name: "Backtest vs paper metrics" });
  await expect(t).toContainText("91.76%");
  await expect(t).toContainText("live: not implemented");
  await expect(panel.locator("svg.chart-interval")).toHaveCount(3);
  await expect(panel.locator("[data-tier]")).toHaveText("UNDECIDED+");
});

test("Feeds and Status: heartbeats, halt, activity", async () => {
  await page.keyboard.press(`${mod}+7`);
  await expect(page.getByRole("heading", { level: 1, name: "Feeds and Status", exact: true })).toBeVisible();
  await expect(page.getByRole("table", { name: "Processes" })).toContainText("runner");
  await expect(page.getByRole("table", { name: "Processes" })).toContainText("1m before report");
  await expect(page.getByRole("table", { name: "Halts" })).toContainText("fixture/eth_no_edge");
  await expect(page.getByRole("table", { name: "Strategy activity" })).toContainText("KXETH15M");
  await expect(page.getByRole("region", { name: "Feeds" })).toContainText("not in status.json");
  await expect(page.getByRole("alert").filter({ hasText: "STALE" })).toBeVisible();
  await expect(page.getByTestId("status-heartbeat")).toContainText("healthy, heartbeat");
});

test("no console errors (including CSP violations)", () => {
  expect(consoleErrors).toEqual([]);
});
