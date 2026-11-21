import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron as electron, expect, test, type ElectronApplication, type Page } from "@playwright/test";

/**
 * DESK-012 Google Sheets source and DESK-020 diagnostics. "Google" is the test transport
 * (NEM_DESK_SHEETS_FIXTURES): tabs are the contract's basic fixture CSVs, nothing touches
 * the network. The sheet ID is made up.
 */

const appDir = join(import.meta.dirname, "..");
const basic = join(appDir, "../../contract/fixtures/basic/out/fixture");
const mod = process.platform === "darwin" ? "Meta" : "Control";
const work = mkdtempSync(join(tmpdir(), "nem-desk-sheets-e2e-"));
const tabs = join(work, "tabs");
const profile = join(work, "profile");
const FAKE_ID = "1FAKEsheetIdForNemDeskTestsOnly_0000000000a";

let app: ElectronApplication;
let page: Page;

test.beforeAll(async () => {
  mkdirSync(tabs, { recursive: true });
  for (const t of ["summary", "strategies", "trades"]) copyFileSync(join(basic, `${t}.csv`), join(tabs, `fixture ${t}.csv`));
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (v !== undefined && k !== "ELECTRON_RUN_AS_NODE") env[k] = v;
  }
  env["NEM_DESK_USER_DATA"] = profile;
  env["NEM_DESK_SHEETS_FIXTURES"] = tabs;
  app = await electron.launch({ args: [appDir], cwd: appDir, env });
  page = await app.firstWindow();
});

test.afterAll(async () => {
  await app.close();
  rmSync(work, { recursive: true, force: true });
});

test("a shared sheet becomes the Google Sheet source", async () => {
  await expect(page.getByRole("status", { name: "No portfolios yet" })).toBeVisible();
  await page.keyboard.press(`${mod}+8`);
  const section = page.getByRole("region", { name: "Google Sheet" });
  await section.getByLabel("Sheet link or ID").fill(`https://docs.google.com/spreadsheets/d/${FAKE_ID}/edit#gid=0`);
  await section.getByLabel("Portfolios").fill("fixture");
  await section.getByLabel("Auto refresh").selectOption("");
  await section.getByRole("button", { name: "Sync now" }).click();
  await expect(section.getByTestId("sheets-last")).toContainText("ok");
  await expect(section.getByRole("table", { name: "Sheet tabs" })).toContainText("fixture trades");
  await expect(page.getByTestId("status-source")).toContainText("Google Sheet");
  // The renderer only ever sees the masked ID.
  await expect(section.getByLabel("Sheet link or ID")).toHaveAttribute("placeholder", /1FAK…000a/);
  expect(await page.content()).not.toContain(FAKE_ID);

  await page.keyboard.press(`${mod}+1`);
  const card = page.getByRole("region", { name: "Portfolio fixture" });
  await expect(card.locator("[data-tier]")).toHaveText(["EDGE", "NO EDGE", "UNDECIDED+"]);
});

test("a private sheet is an error, not a crash", async () => {
  writeFileSync(join(tabs, "_status"), "403");
  await page.keyboard.press(`${mod}+8`);
  const section = page.getByRole("region", { name: "Google Sheet" });
  await section.getByRole("button", { name: "Sync now" }).click();
  await expect(section.getByTestId("sheets-last")).toContainText("not_shared");
  rmSync(join(tabs, "_status"));
});

test("diagnostics show versions and copy a redacted report", async () => {
  const diag = page.getByRole("region", { name: "Diagnostics" });
  await diag.getByRole("button", { name: "Refresh" }).click();
  await expect(diag.getByTestId("diag-contract")).toContainText("1.x");
  await expect(diag.getByTestId("diag-log")).toContainText("[sheets] manual sync");
  await diag.getByRole("button", { name: "Copy diagnostics" }).click();
  await expect(diag.getByRole("status")).toContainText("Copied");
  const text = await app.evaluate(({ clipboard }) => clipboard.readText());
  expect(text).toContain("contract: 1.x");
  expect(text).toContain("Google Sheet [sheets] <userData>/sheets-cache");
  expect(text).not.toContain(FAKE_ID);
  expect(text).not.toContain(profile);

  const log = readFileSync(join(profile, "logs", "main.log"), "utf8");
  expect(log).toContain("[app] start");
  expect(log).toContain("[sheets] manual sync: 3/3 tab(s) ok");
  expect(log).not.toContain(FAKE_ID);
});
