import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { HaltApi, HaltRequest, HaltResult, HaltState } from "@shared/halt";
import type { NemApi } from "@shared/ipc";
import { fakeApi } from "../data/testData";
import { HaltControls, HaltSettings } from ".";

const ON: HaltState = {
  enabled: true,
  target: "nem@nem.example.org",
  runsAs: "cd '/home/nem/not-enough-markets' && .venv/bin/nem",
  portfolios: [{ name: "paper0", mode: "paper" }],
  strategies: ["paper0/fav_90_btc"],
  halts: [{ scope: "paper0/fav_90_btc", reason: "check fills", since: "2026-11-20T12:00:00Z" }],
  running: false,
};

function mount(initial: HaltState, ui: "controls" | "settings", result?: (r: HaltRequest) => HaltResult) {
  const calls = { run: [] as HaltRequest[], enabled: [] as boolean[] };
  let state = initial;
  const halt: HaltApi = {
    get: () => Promise.resolve(state),
    setEnabled: (on) => {
      calls.enabled.push(on);
      state = { ...state, enabled: on };
      return Promise.resolve(state);
    },
    run: (req) => {
      calls.run.push(req);
      return Promise.resolve(result?.(req) ?? { ok: true, command: "ssh nem@host ...", output: "Halted paper0", synced: true, state });
    },
  };
  const api: NemApi = fakeApi().api;
  window.nem = { ...api, sources: { ...api.sources, vps: { ...(api.sources.vps as object), halt } as NemApi["sources"]["vps"] } };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}>{ui === "controls" ? <HaltControls /> : <HaltSettings />}</QueryClientProvider>);
  return { calls, view };
}

afterEach(() => {
  delete window.nem;
});

describe("HaltControls", () => {
  it("renders nothing while the flag is off", async () => {
    const { view } = mount({ ...ON, enabled: false }, "controls");
    await new Promise((r) => setTimeout(r, 10));
    expect(view.container).toBeEmptyDOMElement();
  });

  it("renders nothing without a halt API (test doubles, plain browser)", async () => {
    window.nem = fakeApi().api;
    const client = new QueryClient();
    const view = render(<QueryClientProvider client={client}><HaltControls /></QueryClientProvider>);
    await new Promise((r) => setTimeout(r, 10));
    expect(view.container).toBeEmptyDOMElement();
  });

  it("enables Halt only after the exact scope is typed, then shows the output", async () => {
    const { calls } = mount(ON, "controls");
    const form = within(await screen.findByRole("group", { name: "Halt" }));
    fireEvent.change(form.getByLabelText("Scope"), { target: { value: "paper0/fav_90_btc" } });
    fireEvent.change(form.getByLabelText("Reason"), { target: { value: "check fills" } });
    const go = form.getByRole("button", { name: "Halt paper0/fav_90_btc" });
    expect(go).toBeDisabled();
    const confirm = form.getByLabelText(/to confirm/);
    fireEvent.change(confirm, { target: { value: "paper0" } });
    expect(go).toBeDisabled();
    fireEvent.change(confirm, { target: { value: "paper0/fav_90_btc" } });
    expect(go).toBeEnabled();
    fireEvent.click(go);
    await waitFor(() => expect(calls.run).toHaveLength(1));
    expect(calls.run[0]).toEqual({ action: "halt", scope: "paper0/fav_90_btc", reason: "check fills", confirm: "paper0/fav_90_btc" });
    expect(await form.findByTestId("halt-result")).toHaveTextContent("Halted paper0");
  });

  it("needs a reason to halt", async () => {
    mount(ON, "controls");
    const form = within(await screen.findByRole("group", { name: "Halt" }));
    fireEvent.change(form.getByLabelText(/to confirm/), { target: { value: "*" } });
    expect(form.getByRole("button", { name: "Halt *" })).toBeDisabled();
  });

  it("disables live and unknown-mode portfolios and explains why", async () => {
    mount({ ...ON, portfolios: [{ name: "paper0", mode: "paper" }, { name: "real", mode: "live" }] }, "controls");
    const form = within(await screen.findByRole("group", { name: "Halt" }));
    expect(form.getByTestId("halt-halt-blocked")).toHaveTextContent("real (live)");
    expect(form.queryByRole("button", { name: /^Halt/ })).toBeNull();
    fireEvent.change(form.getByLabelText("Scope"), { target: { value: "real" } });
    expect(form.getByTestId("halt-halt-blocked")).toHaveTextContent("live portfolio");
    fireEvent.change(form.getByLabelText("Scope"), { target: { value: "paper0" } });
    expect(form.queryByTestId("halt-halt-blocked")).toBeNull();
  });

  it("resumes only listed halts and shows a failure as reported by main", async () => {
    const { calls } = mount(ON, "controls", () => ({
      ok: false,
      command: "ssh nem@host ...",
      output: "",
      error: { kind: "host_key", message: "The host key is unknown." },
      state: ON,
    }));
    const form = within(await screen.findByRole("group", { name: "Resume" }));
    expect(form.getAllByRole("option").map((o) => o.textContent)).toEqual(["paper0/fav_90_btc"]);
    fireEvent.change(form.getByLabelText(/to confirm/), { target: { value: "paper0/fav_90_btc" } });
    fireEvent.click(form.getByRole("button", { name: "Resume paper0/fav_90_btc" }));
    await waitFor(() => expect(calls.run).toHaveLength(1));
    expect(await form.findByTestId("halt-result")).toHaveTextContent("Failed (host_key)");
  });
});

describe("HaltSettings", () => {
  it("toggles the flag through main", async () => {
    const { calls } = mount({ ...ON, enabled: false }, "settings");
    const box = await screen.findByLabelText("Enable halt/resume over SSH (experimental)");
    await waitFor(() => expect(box).toBeEnabled());
    expect(box).not.toBeChecked();
    fireEvent.click(box);
    await waitFor(() => expect(box).toBeChecked());
    expect(calls.enabled).toEqual([true]);
  });
});
