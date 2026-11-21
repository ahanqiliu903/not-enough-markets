import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  retries: process.env["CI"] !== undefined ? 1 : 0,
  workers: 1,
  reporter: process.env["CI"] !== undefined ? [["list"], ["html", { open: "never" }]] : "list",
  use: { trace: "retain-on-failure" },
});
