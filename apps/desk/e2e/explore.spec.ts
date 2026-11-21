import { join } from "node:path";
import { _electron as electron, expect, test, type ElectronApplication, type Page } from "@playwright/test";

// Explore (DESK-024) against recorded Kalshi responses: NEM_DESK_KALSHI_FIXTURES makes the
// main process serve e2e/fixtures/kalshi/*.json instead of the network (unpackaged builds only).
const appDir = join(import.meta.dirname, "..");
const mod = process.platform === "darwin" ? "Meta" : "Control";

let app: ElectronApplication;
let page: Page;
const consoleErrors: string[] = [];

function launchEnv(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (v !== undefined && k !== "ELECTRON_RUN_AS_NODE") env[k] = v;
  }
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
});

test.afterAll(async () => {
  await app.close();
});

test("mod+9 opens Explore with category tabs and series tiles", async () => {
  await expect(page.getByRole("navigation", { name: "Screens" })).toBeVisible();
  await page.keyboard.press(`${mod}+9`);
  await expect(page.getByRole("heading", { level: 1, name: "Explore", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Crypto 15m" })).toHaveAttribute("aria-selected", "true");
  const btc = page.getByRole("button", { name: "Open series KXBTC15M" });
  await expect(btc.getByTestId("open-count")).toHaveText(/^\d+$/);
  // No recording for ETH: the tile shows the error value, not a crash.
  await expect(page.getByRole("button", { name: "Open series KXETH15M" })).toContainText("not found");
});

test("series -> market list with rules card -> market page with YES/NO book", async () => {
  await page.getByRole("tab", { name: "Daily high" }).click();
  await page.getByRole("button", { name: "Open series KXHIGHNY" }).click();
  const table = page.getByRole("table", { name: "Markets" });
  await expect(table.getByRole("row")).toHaveCount(13);
  const row = table.getByRole("row").filter({ hasText: "KXHIGHNY-26NOV21-B62.5" });
  await row.getByText("?").hover();
  const card = page.getByRole("tooltip");
  await expect(card).toContainText("The Weather Company");
  await expect(card).toContainText("Closes (ET)");
  await expect(card).toContainText("nws_forecast");
  await row.getByRole("button", { name: "KXHIGHNY-26NOV21-B62.5" }).click();

  const yes = page.getByRole("region", { name: "YES side" });
  await expect(yes.getByTestId("yes-buy")).toHaveText(/^\d+(\.\d)?c$/);
  await expect(page.getByRole("region", { name: "NO side" }).getByTestId("no-sell")).toHaveText(/^\d+(\.\d)?c$/);
  await expect(yes.getByRole("table", { name: "Sell YES (bids, best first)" }).getByRole("row")).toHaveCount(6);
});

test("start a strategy here copies the CLI command", async () => {
  await page.getByRole("button", { name: "Start a strategy here" }).click();
  await expect(page.getByText(/Copied\./)).toBeVisible();
  const text = await app.evaluate(({ clipboard }) => clipboard.readText());
  expect(text).toBe("uv run nem strategy add PORTFOLIO NAME --series KXHIGHNY");
});

test("pixel accents off does not move the explore layout", async () => {
  const boxes = () =>
    page.evaluate(() =>
      [".book-side", ".explore-crumbs", ".tob-chart, .chart-empty", ".start-strategy"].map((sel) => {
        const r = document.querySelector(sel)?.getBoundingClientRect();
        return r ? [r.x, r.y, r.width, r.height] : null;
      }),
    );
  const before = await boxes();
  expect(before.every((b) => b !== null)).toBe(true);
  const toggle = async () => {
    await page.keyboard.press(`${mod}+k`);
    await page.keyboard.type("pixel accents");
    await page.keyboard.press("Enter");
  };
  await toggle();
  await expect(page.locator("html")).toHaveAttribute("data-pixel", "off");
  expect(await boxes()).toEqual(before);
  await toggle();
  await expect(page.locator("html")).toHaveAttribute("data-pixel", "on");
});

test("command bar: explore SERIES, Esc goes back", async () => {
  await page.keyboard.press(`${mod}+k`);
  await page.keyboard.type("explore KXBTC15M");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Series KXBTC15M" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("region", { name: "YES side" })).toBeVisible();
});

test("no console errors (including CSP violations)", () => {
  expect(consoleErrors).toEqual([]);
});
