import { useQueryClient } from "@tanstack/react-query";
import { Suspense, useEffect, useMemo } from "react";
import { PaneTitle } from "../components/PaneTitle";
import { PixelIcon } from "../components/PixelIcon";
import { EmptyState } from "../components/states";
import { useDataSync } from "../data/hooks";
import { comboLabel, KEY_PRIORITY, useKeys, type KeyBindings } from "../lib/keys";
import { routeById, visibleRoutes } from "../routes";
import { exploreSeedCommands } from "../screens/explore/commands";
import { useRegisterCommands, type Command } from "../state/commands";
import { useNav } from "../state/nav";
import { useSettings } from "../state/settings";
import { CommandPalette } from "./CommandPalette";
import { NavPane, Splitter } from "./NavPane";
import { ScreenErrorBoundary } from "./ScreenErrorBoundary";
import { StatusBar } from "./StatusBar";

/** Focuses the current screen's filter input, if it has one (`data-screen-filter`). */
function focusScreenFilter(): boolean {
  const el = document.querySelector<HTMLInputElement>("main [data-screen-filter]");
  if (el === null) return false;
  el.focus();
  el.select();
  return true;
}

const EXPLORE_COMMANDS = exploreSeedCommands();

export function Shell() {
  const screen = useNav((s) => s.screen);
  const flags = useNav((s) => s.flags);
  const navigate = useNav((s) => s.navigate);
  const back = useNav((s) => s.back);
  const setPaletteOpen = useNav((s) => s.setPaletteOpen);
  const navWidth = useSettings((s) => s.navWidth);
  const pixelAccents = useSettings((s) => s.pixelAccents);
  const setPixelAccents = useSettings((s) => s.setPixelAccents);
  const queryClient = useQueryClient();

  const routes = useMemo(() => visibleRoutes(flags), [flags]);
  const current = routes.find((r) => r.id === screen) ?? routeById("portfolios");

  // If the current screen becomes hidden (e.g. backtests data disappears), go home.
  useEffect(() => {
    if (!routes.some((r) => r.id === screen)) navigate("portfolios");
  }, [routes, screen, navigate]);

  const refresh = useMemo(() => () => void queryClient.invalidateQueries(), [queryClient]);

  const commands = useMemo<Command[]>(
    () => [
      ...routes.map((r) => ({
        id: `screen:${r.id}`,
        title: r.title,
        group: "Screen",
        keywords: r.keywords ?? "",
        ...(r.hotkey !== undefined ? { shortcut: `mod+${r.hotkey}` } : {}),
        run: () => navigate(r.id),
      })),
      { id: "action:refresh", title: "Refresh data", group: "Action", shortcut: "r", run: refresh },
      {
        id: "action:pixel-accents",
        title: `Pixel accents: turn ${pixelAccents ? "off" : "on"}`,
        group: "Action",
        keywords: "font icons retro",
        run: () => setPixelAccents(!pixelAccents),
      },
      {
        id: "source:manage",
        title: "Switch data source",
        group: "Source",
        keywords: "sqlite csv export open",
        run: () => navigate("settings"),
      },
    ],
    [routes, navigate, refresh, pixelAccents, setPixelAccents],
  );
  useRegisterCommands(commands);
  useDataSync();
  useRegisterCommands(EXPLORE_COMMANDS);

  const bindings: KeyBindings = {
    "mod+k": () => setPaletteOpen(true),
    "/": () => focusScreenFilter(),
    r: refresh,
    Escape: () => back(),
  };
  for (const r of routes) {
    if (r.hotkey !== undefined) bindings[`mod+${r.hotkey}`] = () => navigate(r.id);
  }
  useKeys(bindings, { priority: KEY_PRIORITY.shell });

  const Screen = current?.component;
  return (
    <div className="shell" style={{ gridTemplateColumns: `${navWidth}px 4px 1fr` }}>
      <header className="command-bar">
        <span className="command-brand">
          <PixelIcon name="desk" />
          <span className="pixel-font">NEM DESK</span>
        </span>
        <button
          type="button"
          className="command-trigger"
          onClick={() => setPaletteOpen(true)}
          aria-label="Open command palette"
        >
          <span className="dim">Jump to screen, portfolio, strategy...</span>
          <kbd>{comboLabel("mod+k")}</kbd>
        </button>
        <span className="command-mode">PAPER / READ-ONLY</span>
      </header>
      <NavPane routes={routes} />
      <Splitter />
      <main className="main-pane" aria-label={current?.title ?? "Screen"}>
        {current !== undefined && (
          <PaneTitle title={current.title} icon={current.icon} />
        )}
        <div className="pane-body" data-screen={current?.id}>
          <ScreenErrorBoundary key={current?.id} screen={current?.id ?? "unknown"}>
            <Suspense fallback={<p className="dim">Loading...</p>}>
              {Screen !== undefined ? (
                <Screen />
              ) : (
                <EmptyState title="Unknown screen" reason="This screen is not in the route table." />
              )}
            </Suspense>
          </ScreenErrorBoundary>
        </div>
      </main>
      <StatusBar />
      <CommandPalette />
    </div>
  );
}
