import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron as electron, expect, test, type ElectronApplication, type Page } from "@playwright/test";

// DESK-019 against a recorded releases list: NEM_DESK_UPDATE_FIXTURE makes main answer the
// GitHub request from e2e/fixtures/github/releases.json (unpackaged builds only). Dev builds
// never check on their own, so no other spec makes an update request either.
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
  env["NEM_DESK_UPDATE_FIXTURE"] = join(appDir, "e2e", "fixtures", "github", "releases.json");
  return env;
}

test.beforeAll(async () => {
  app = await electron.launch({ args: [appDir], cwd: appDir, env: launchEnv() });
  page = await app.firstWindow();
  // Record instead of opening a browser.
  await app.evaluate(({ shell }) => {
    const opened: string[] = [];
    (globalThis as { __opened?: string[] }).__opened = opened;
    shell.openExternal = (url: string) => {
      opened.push(url);
      return Promise.resolve();
    };
  });
});

test.afterAll(async () => {
  await app.close();
});

test("no notice before a check; Check now finds the newer release; the notice opens its page", async () => {
  await expect(page.getByTestId("status-version")).toBeVisible();
  await expect(page.getByTestId("update-notice")).toHaveCount(0);

  await page.keyboard.press(`${mod}+8`);
  const section = page.getByRole("region", { name: "Updates" });
  await expect(section.getByTestId("update-result")).toHaveText("Not checked yet.");
  await section.getByRole("button", { name: "Check now" }).click();
  await expect(section.getByTestId("update-result")).toContainText("v99.0.0 is available");

  const notice = page.getByRole("button", { name: "update v99.0.0 available" });
  await expect(notice).toBeVisible();
  await notice.click();
  await expect
    .poll(() => app.evaluate(() => (globalThis as { __opened?: string[] }).__opened ?? []))
    .toEqual(["https://github.com/ahanqiliu903/not-enough-markets/releases/tag/desk-v99.0.0"]);

  const daily = section.getByLabel("Check daily");
  await expect(daily).toBeChecked();
  await daily.click();
  await expect(daily).not.toBeChecked();
});
