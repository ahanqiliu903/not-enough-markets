import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { NemApi } from "@shared/ipc";
import type { UpdateCheck, UpdateState, UpdatesApi } from "@shared/updates";
import { fakeApi } from "../data/testData";
import { UpdateNotice, UpdateSettings } from ".";

const AT = "2026-11-20T12:00:00Z";
const FOUND: UpdateCheck = {
  ok: true,
  at: AT,
  current: "0.1.0",
  updateAvailable: true,
  latest: {
    version: "0.2.0",
    tag: "desk-v0.2.0",
    name: "NEM Desk 0.2.0",
    url: "https://github.com/ahanqiliu903/not-enough-markets/releases/tag/desk-v0.2.0",
    publishedAt: AT,
  },
};

function mount(initial: UpdateState, ui: "notice" | "settings", after?: UpdateState) {
  const calls = { open: 0, check: 0, enabled: [] as boolean[] };
  let state = initial;
  const updates: UpdatesApi = {
    get: () => Promise.resolve(state),
    check: () => {
      calls.check++;
      state = after ?? state;
      return Promise.resolve(state);
    },
    setEnabled: (on) => {
      calls.enabled.push(on);
      state = { ...state, enabled: on };
      return Promise.resolve(state);
    },
    openRelease: () => {
      calls.open++;
      return Promise.resolve(true);
    },
  };
  const api: NemApi = fakeApi().api;
  window.nem = { ...api, data: { ...api.data, updates } };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{ui === "notice" ? <UpdateNotice /> : <UpdateSettings />}</QueryClientProvider>);
  return calls;
}

const base: UpdateState = { enabled: true, current: "0.1.0", checking: false, last: null };

afterEach(() => {
  delete window.nem;
});

describe("UpdateNotice", () => {
  it("links to the release when a newer version exists", async () => {
    const calls = mount({ ...base, last: FOUND }, "notice");
    const link = await screen.findByRole("button", { name: "update v0.2.0 available" });
    fireEvent.click(link);
    expect(calls.open).toBe(1);
  });
  it("stays silent when up to date or on errors", async () => {
    mount({ ...base, last: { ok: false, at: AT, current: "0.1.0", error: { kind: "offline", message: "GitHub unreachable" } } }, "notice");
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("update-notice")).toBeNull();
    expect(screen.queryByText(/unreachable/)).toBeNull();
  });
});

describe("UpdateSettings", () => {
  it("shows errors, checks now and toggles daily checks", async () => {
    const failed: UpdateState = {
      ...base,
      last: { ok: false, at: AT, current: "0.1.0", error: { kind: "rate_limited", message: "GitHub rate limit (HTTP 403)" } },
    };
    const calls = mount(failed, "settings", { ...base, last: FOUND });
    expect(await screen.findByRole("alert")).toHaveTextContent("GitHub rate limit (HTTP 403)");
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    await waitFor(() => expect(screen.getByTestId("update-result")).toHaveTextContent("v0.2.0 is available"));
    expect(calls.check).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Open v0.2.0 release page" }));
    expect(calls.open).toBe(1);
    fireEvent.click(screen.getByLabelText("Check daily"));
    await waitFor(() => expect(calls.enabled).toEqual([false]));
    await waitFor(() => expect(screen.getByLabelText("Check daily")).not.toBeChecked());
  });
  it("says when no release exists yet", async () => {
    mount({ ...base, last: { ok: true, at: AT, current: "0.1.0", latest: null, updateAvailable: false } }, "settings");
    expect(await screen.findByTestId("update-result")).toHaveTextContent("No NEM Desk release published yet");
  });
});
