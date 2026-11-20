import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron as electron, expect, test, type ElectronApplication, type Page } from "@playwright/test";

/** DESK-003..009: sample data, the four data screens, file watching, 100k trades. */

const appDir = join(import.meta.dirname, "..");
const fixtures = join(appDir, "../../contract/fixtures");
const mod = process.platform === "darwin" ? "Meta" : "Control";
const work = mkdtempSync(join(tmpdir(), "nem-desk-data-e2e-"));

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

async function addFolder(path: string): Promise<void> {
  const ok = await page.evaluate(async (p) => (await window.nem?.sources.addFolder(p))?.ok, path);
  expect(ok).toBe(true);
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

test("loads sample data: Portfolios shows the fixture with three verdict badges", async () => {
  await expect(page.getByRole("status", { name: "No portfolios yet" })).toBeVisible();
  await page.getByRole("button", { name: "Load sample data" }).click();
  const card = page.getByRole("region", { name: "Portfolio fixture" });
  await expect(card).toBeVisible();
  await expect(card.locator("[data-tier]")).toHaveText(["EDGE", "NO EDGE", "UNDECIDED+"]);
  await expect(card).toContainText("$1059.14");
  await expect(page.getByRole("alert").filter({ hasText: "STALE" })).toBeVisible();
  await expect(page.getByTestId("status-source")).toContainText("Sample data");
});

test("opens a strategy with its intervals, SPRT progress and P&L curve", async () => {
  await page.getByRole("region", { name: "Portfolio fixture" }).getByText("btc_edge", { exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Strategy", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Sequential test progress" })).toContainText("170 settled of 33");
  await expect(page.getByRole("region", { name: "Win rate vs break-even" })).toContainText("99.41%");
  await expect(page.locator(".chart-line canvas").first()).toBeVisible();
  await expect(page.getByRole("table", { name: "Calibration bins" })).toBeVisible();
});

test("trades blotter shows rows and sorts", async () => {
  await page.keyboard.press(`${mod}+4`);
  await expect(page.getByRole("heading", { level: 1, name: "Trades", exact: true })).toBeVisible();
  await expect(page.getByTestId("trade-count")).toContainText("of 394 trades");
  await expect(page.getByRole("row").nth(1)).toContainText("KX");
  await page.getByRole("columnheader", { name: "P&L" }).click();
  await expect(page.getByRole("columnheader", { name: /P&L/ })).toHaveAttribute("aria-sort", /ascending|descending/);
});

test("compare shows two strategies side by side", async () => {
  await page.keyboard.press(`${mod}+3`);
  await page.getByRole("checkbox", { name: /^btc_edge/ }).check();
  await page.getByRole("checkbox", { name: /^sol_undecided/ }).check();
  const table = page.getByRole("table", { name: "Compare statistics" });
  await expect(table.locator("[data-tier]")).toHaveText(["EDGE", "UNDECIDED+"]);
  await expect(page.locator(".chart-line canvas").first()).toBeVisible();
});

test("a changed CSV shows within 2 s; a deleted one is an error, not a crash", async () => {
  const src = join(work, "nem-cache");
  cpSync(join(fixtures, "basic"), src, { recursive: true });
  rmSync(join(src, "nem.db"), { force: true });
  await addFolder(src);
  await page.keyboard.press(`${mod}+1`);
  const card = page.getByRole("region", { name: "Portfolio fixture" });
  await expect(card).toContainText("$1059.14");

  const summary = join(src, "out/fixture/summary.csv");
  const tmp = `${summary}.tmp`;
  writeFileSync(tmp, readFileSync(summary, "utf8").replace("1059.14", "1234.56"));
  // Atomic replace, as the engine's reporter does.
  cpSync(tmp, summary);
  rmSync(tmp);
  await expect(card).toContainText("$1234.56", { timeout: 2000 });

  rmSync(join(src, "out/fixture/strategies.csv"));
  const err = page.getByRole("alert", { name: "Schema error" });
  await expect(err).toContainText("out/fixture/strategies.csv", { timeout: 2000 });
  await expect(card).toContainText("$1234.56");
});

test("100k trades: blotter scrolls and sorts quickly", async () => {
  const src = join(work, "big");
  const dir = join(src, "out", "big");
  mkdirSync(dir, { recursive: true });
  const base = join(fixtures, "basic/out/fixture");
  for (const f of ["summary.csv", "strategies.csv"]) {
    writeFileSync(join(dir, f), readFileSync(join(base, f), "utf8").replaceAll(",fixture,", ",big,"));
  }
  const header = readFileSync(join(base, "trades.csv"), "utf8").split(/\r?\n/)[0] ?? "";
  const lines = [header];
  for (let i = 0; i < 100_000; i++) {
    const h = String(i % 24).padStart(2, "0");
    const m = String(i % 60).padStart(2, "0");
    const won = i % 10 !== 0;
    lines.push(
      `1.0,big,btc_edge,W${i},T${i}-00,yes,5,${88 + (i % 5)}.0,0.04,89.8,paper,2026-10-01T${h}:${m}:00Z,2026-10-01T${h}:${m}:30Z,2026-10-01T${h}:${m}:33Z,${won ? "won" : "lost"},${won ? "0.51" : "-4.49"}`,
    );
  }
  writeFileSync(join(dir, "trades.csv"), `${lines.join("\n")}\n`);
  await addFolder(src);
  await page.keyboard.press(`${mod}+4`);
  await expect(page.getByTestId("trade-count")).toContainText("100,000 of 100,000", { timeout: 15_000 });
  expect(await page.getByRole("row").count()).toBeLessThan(150);

  const blotter = page.getByRole("table", { name: "Trades" });
  await blotter.evaluate((el) => {
    el.scrollTop = 1_000_000;
  });
  await expect(page.locator('[aria-rowindex="50001"]')).toBeAttached();
  await expect(page.locator('[aria-rowindex="50001"]')).not.toContainText("...");

  const ms = await page.evaluate(async () => {
    const t0 = performance.now();
    const r = await window.nem?.data.trades({ sort: { column: "ticker", dir: "asc" }, offset: 0, limit: 200 });
    if (r?.ok !== true) throw new Error("trades failed");
    const t1 = performance.now();
    await window.nem?.data.trades({ sort: { column: "ticker", dir: "asc" }, offset: 80_000, limit: 200 });
    return [t1 - t0, performance.now() - t1];
  });
  console.log(`100k: new sort ${ms[0]?.toFixed(0)} ms, scroll page ${ms[1]?.toFixed(1)} ms`);
  expect(ms[1]).toBeLessThan(100);
  expect(ms[0]).toBeLessThan(1000);
});

test("no console errors (including CSP violations)", () => {
  expect(consoleErrors).toEqual([]);
});
