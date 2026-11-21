import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { homedir, tmpdir, userInfo } from "node:os";
import { join, resolve } from "node:path";
import { _electron as electron, expect, test, type ElectronApplication, type Page } from "@playwright/test";

/**
 * DESK-022/023: walks the main screens on "Load sample data" (contract/fixtures/basic) and the
 * recorded Kalshi responses (no network), with a throwaway profile. Checks that pixel accents
 * off leaves no pixel font, icon or pixel frame on any of them, and that no screen shows a
 * home path or the OS user. With NEM_SCREENSHOT_DIR set (`npm run screenshots`), it saves the
 * README screenshots (pixel accents on) there.
 */

const appDir = join(import.meta.dirname, "..");
const repoRoot = resolve(appDir, "..", "..");
const mod = process.platform === "darwin" ? "Meta" : "Control";
const work = mkdtempSync(join(tmpdir(), "nem-desk-shots-"));
const shotDir = process.env["NEM_SCREENSHOT_DIR"];

let app: ElectronApplication;
let page: Page;
const consoleErrors: string[] = [];

function launchEnv(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (v !== undefined && k !== "ELECTRON_RUN_AS_NODE") env[k] = v;
  }
  env["NEM_DESK_USER_DATA"] = join(work, "profile");
  env["NEM_DESK_KALSHI_FIXTURES"] = join(appDir, "e2e", "fixtures", "kalshi");
  return env;
}

test.beforeAll(async () => {
  app = await electron.launch({ args: [appDir], cwd: appDir, env: launchEnv() });
  page = await app.firstWindow();
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(err.message));
  await app.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()[0]?.setContentSize(1280, 800);
  });
  if (shotDir !== undefined) mkdirSync(resolve(appDir, shotDir), { recursive: true });
});

test.afterAll(async () => {
  await app.close();
  rmSync(work, { recursive: true, force: true });
});

async function shot(name: string): Promise<void> {
  // The sample DB's absolute path (Market screen) becomes repo-relative; after that nothing
  // private may be on screen: no home folder, no OS user name.
  // The throwaway profile lives in the OS temp folder; show it as the app's data folder.
  await page.evaluate(
    ([prefix, profile]) => {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n !== null; n = walker.nextNode()) {
        if (n.textContent?.includes(profile) === true) n.textContent = n.textContent.replaceAll(profile, "<app data>");
        if (n.textContent?.includes(prefix) === true) n.textContent = n.textContent.replaceAll(prefix, "");
      }
    },
    [`${repoRoot}/`, join(work, "profile")] as const,
  );
  const text = await page.evaluate(() => document.body.innerText);
  expect(text).not.toContain(homedir());
  // The OS user as it would leak: in a path or as user@host. A bare substring check is wrong
  // on CI, where the user is "runner" and the sample status shows a process named runner.
  const user = userInfo().username.toLowerCase();
  const lower = text.toLowerCase();
  for (const leak of [`/${user}/`, `\\${user}\\`, `${user}@`]) expect(lower).not.toContain(leak);
  expect(text).not.toContain(work);
  if (shotDir === undefined) return;
  await page.mouse.move(0, 0);
  await page.screenshot({ path: resolve(appDir, shotDir, `${name}.png`), scale: "css", animations: "disabled" });
}

/** Opens each screen in turn; `visit` runs once per screen after it has loaded. */
async function tour(visit: (name: string) => Promise<void>): Promise<void> {
  await page.keyboard.press(`${mod}+1`);
  const card = page.getByRole("region", { name: "Portfolio fixture" });
  await expect(card.locator("[data-tier]")).toHaveText(["EDGE", "NO EDGE", "UNDECIDED+"]);
  await visit("portfolios");

  await card.getByText("btc_edge", { exact: true }).click();
  await expect(page.getByRole("region", { name: "Sequential test progress" })).toBeVisible();
  await expect(page.locator(".chart-line canvas").first()).toBeVisible();
  await visit("strategy");

  await page.keyboard.press(`${mod}+4`);
  await expect(page.getByTestId("trade-count")).toContainText("trades");
  await visit("trades");

  await page.keyboard.press(`${mod}+6`);
  await page.getByRole("table", { name: "Backtest runs" }).getByText("sol_undecided", { exact: true }).click();
  const heatmap = page.getByRole("table", { name: "Sweep heatmap" });
  await expect(heatmap.locator("td.heat-chosen")).toHaveCount(1);
  await heatmap.scrollIntoViewIfNeeded();
  await visit("backtests");

  await page.keyboard.press(`${mod}+5`);
  const market = page.getByRole("region", { name: /^Market KXBTC15M-/ });
  await expect(market.locator("canvas.depth-canvas")).toBeVisible();
  await visit("market");

  await page.keyboard.press(`${mod}+9`);
  await page.getByRole("tab", { name: "Daily high" }).click();
  await page.getByRole("button", { name: "Open series KXHIGHNY" }).click();
  await page.getByRole("table", { name: "Markets" }).getByRole("button", { name: "KXHIGHNY-26NOV21-B62.5" }).click();
  await expect(page.getByRole("region", { name: "YES side" })).toBeVisible();
  await visit("explore");

  await page.keyboard.press(`${mod}+7`);
  await expect(page.getByRole("table", { name: "Processes" })).toBeVisible();
  await visit("status");
}

test("load sample data", async () => {
  await expect(page.getByRole("button", { name: "Load sample data" })).toBeVisible();
  await shot("welcome");
  await page.getByRole("button", { name: "Load sample data" }).click();
  await expect(page.getByRole("region", { name: "Portfolio fixture" })).toBeVisible();
});

test("main screens with pixel accents on", async () => {
  await expect(page.locator("html")).toHaveAttribute("data-pixel", "on");
  await tour(async (name) => {
    // Every screen has its pixel icon in the nav and pane title.
    await expect(page.locator(".pane-title svg.pixel-icon")).toBeVisible();
    await shot(name);
  });
  // The lore card on Strategy, for the README.
  await page.keyboard.press(`${mod}+1`);
  await page.getByRole("region", { name: "Portfolio fixture" }).getByText("btc_edge", { exact: true }).click();
  await page.locator(".pane-body [data-tier]").first().hover();
  await expect(page.getByRole("tooltip")).toBeVisible();
  if (shotDir !== undefined) {
    await page.screenshot({ path: resolve(appDir, shotDir, "lore-card.png"), scale: "css", animations: "disabled" });
  }
  // Sources and Settings, and the command palette, for the README tutorial.
  await page.keyboard.press("Escape");
  await page.keyboard.press(`${mod}+8`);
  await expect(page.getByRole("table", { name: "Sources" })).toBeVisible();
  await shot("settings");
  await page.keyboard.press(`${mod}+k`);
  await page.keyboard.type("explore KXHIGH");
  await expect(page.getByRole("dialog")).toBeVisible();
  await shot("palette");
  await page.keyboard.press("Escape");
});

test("pixel accents off: no pixel font, icon or pixel frame on any screen", async () => {
  await page.keyboard.press(`${mod}+k`);
  await page.keyboard.type("pixel accents");
  await page.keyboard.press("Enter");
  await expect(page.locator("html")).toHaveAttribute("data-pixel", "off");
  await tour(async (name) => {
    const found = await page.evaluate(() => {
      const all = Array.from(document.querySelectorAll("*"));
      const bad: string[] = all.length < 100 ? [`only ${all.length} elements`] : [];
      for (const el of all) {
        const cs = getComputedStyle(el);
        const label = `${el.tagName.toLowerCase()}.${String(el.getAttribute("class"))}`;
        if (cs.fontFamily.includes("Silkscreen")) bad.push(`font: ${label}`);
        if (el.matches(".pixel-icon") && cs.visibility !== "hidden") bad.push(`icon: ${label}`);
        if (cs.boxShadow !== "none" && el.matches(".pixel-border, .card, .panel-box, .state, .tier-badge")) {
          bad.push(`frame: ${label}`);
        }
      }
      return bad;
    });
    expect(found, name).toEqual([]);
    if (name === "portfolios") await shot("portfolios-plain");
  });
  await page.keyboard.press(`${mod}+k`);
  await page.keyboard.type("pixel accents");
  await page.keyboard.press("Enter");
  await expect(page.locator("html")).toHaveAttribute("data-pixel", "on");
});

test("no console errors (including CSP violations)", () => {
  expect(consoleErrors).toEqual([]);
});
