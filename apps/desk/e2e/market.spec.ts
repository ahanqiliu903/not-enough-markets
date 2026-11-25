import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron as electron, expect, test, type ElectronApplication, type Page } from "@playwright/test";

/**
 * DESK-010/011/013: the Market screen on the fixture's nem.db (read-only), attaching a DB,
 * refusing a live one, and VPS settings validation. Sync itself is never run here: no test
 * contacts a server (spawn is mocked in src/main/vps/vps.test.ts).
 */

const appDir = join(import.meta.dirname, "..");
const fixtureDb = join(appDir, "../../contract/fixtures/basic/nem.db");
const mod = process.platform === "darwin" ? "Meta" : "Control";
const work = mkdtempSync(join(tmpdir(), "nem-desk-market-e2e-"));

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

test("Market without a source explains where data comes from", async () => {
  await expect(page.getByRole("heading", { level: 1, name: "Portfolios", exact: true })).toBeVisible();
  await page.keyboard.press(`${mod}+5`);
  await expect(page.getByRole("heading", { level: 1, name: "Market", exact: true })).toBeVisible();
  await expect(page.getByRole("status", { name: "No market data" })).toContainText("data/snapshot/nem.db");
});

test("sample data: series, markets, top of book, depth canvas and feed overlay", async () => {
  await page.getByRole("button", { name: "Load sample data" }).click();
  await expect(page.getByTestId("market-db")).toContainText("schema v9");
  await expect(page.getByRole("tab", { name: /KXBTC15M/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("market-count")).toContainText("of 170");
  const detail = page.getByRole("region", { name: /^Market KXBTC15M-/ });
  await expect(detail.getByTestId("market-result")).toContainText(/YES|NO/);
  await expect(detail.locator(".chart-line canvas").first()).toBeVisible();
  await expect(detail.locator("canvas.depth-canvas")).toBeVisible();
  await expect(detail.getByRole("table", { name: "Order book levels" }).locator("tbody tr")).toHaveCount(3);
  await detail.getByLabel("Feed overlay").selectOption("btc_spot/BTC-USD");
  await expect(detail).toContainText("btc_spot BTC-USD at known_at (16 values");

  await page.getByRole("tab", { name: /KXETH15M/ }).click();
  await expect(page.getByRole("region", { name: /^Market KXETH15M-/ })).toBeVisible();
  await page.keyboard.press("j");
  await expect(page.locator("table[aria-label='Recorded markets'] tr.selected")).toHaveCount(1);
});

test("attaching a database: a live one (-wal beside it) is refused, a snapshot is used", async () => {
  const live = join(work, "live");
  mkdirSync(live);
  copyFileSync(fixtureDb, join(live, "nem.db"));
  writeFileSync(join(live, "nem.db-wal"), "");
  const refused = await page.evaluate(async (p) => window.nem?.sources.attachDb("sample", p), join(live, "nem.db"));
  expect(refused?.ok).toBe(false);
  expect(refused?.ok === false ? refused.message : "").toContain("live database");

  const snap = join(work, "snap.db");
  copyFileSync(fixtureDb, snap);
  const attached = await page.evaluate(async (p) => window.nem?.sources.attachDb("sample", p), snap);
  expect(attached?.ok).toBe(true);
  await page.keyboard.press(`${mod}+8`);
  await expect(page.getByTestId("source-db-sample")).toContainText(`${snap} (attached)`);
  await page.getByRole("button", { name: "Detach database from Sample data" }).click();
  await expect(page.getByTestId("source-db-sample")).not.toContainText("(attached)");
});

test("VPS settings validate in main and are kept in userData only", async () => {
  const section = page.getByRole("region", { name: "VPS sync" });
  await expect(section).toContainText("known_hosts");
  await section.getByLabel("Host").fill("nem.example.org");
  await section.getByLabel("User").fill("root");
  await section.getByLabel("Key path").fill(join(work, "no-such-key"));
  await section.getByRole("button", { name: "Save" }).click();
  await expect(section).toContainText("Key file not found");

  const key = join(work, "id_test");
  writeFileSync(key, "placeholder, not a key");
  await section.getByLabel("Key path").fill(key);
  await section.getByRole("button", { name: "Save" }).click();
  await expect(section.getByRole("button", { name: "Forget server" })).toBeVisible();
  const state = await page.evaluate(async () => window.nem?.sources.vps.get());
  expect(state?.config).toMatchObject({ host: "nem.example.org", user: "root", port: 22, remoteBase: "/home/nem/not-enough-markets" });
  expect(state?.cacheDir).toContain(join(work, "profile"));
  await section.getByRole("button", { name: "Forget server" }).click();
  await expect(section.getByRole("button", { name: "Forget server" })).toHaveCount(0);
});

test("no console errors (including CSP violations)", () => {
  expect(consoleErrors).toEqual([]);
});
