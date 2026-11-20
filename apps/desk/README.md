# NEM Desk

A read-only desktop app for looking at NotEnoughMarkets results: portfolios, strategy
verdicts, trades, market data and engine status. It reads files the engine exports and
never computes statistics, places orders, or holds Kalshi keys.

Status: app shell only (DESK-001, DESK-002, DESK-005). Screens show empty states until the
data layer (DESK-003) and the screen tickets land.

## Run

Requires Node 24 and npm.

```bash
cd apps/desk
npm ci
npm run dev        # Vite dev server + Electron with hot reload
npm run build      # production bundle into out/
npm run dist       # build + unsigned macOS .dmg into release/
npm run check      # lint + typecheck + unit tests
npm run e2e        # build, then Playwright drives the real Electron app
```

| Script | What it does |
|---|---|
| `lint` | ESLint (typescript-eslint strict, type-checked; react-hooks) and `scripts/check-no-hex.mjs` (no hex colors outside `src/renderer/theme.ts`) |
| `typecheck` | `tsc` for main/preload (`tsconfig.node.json`), renderer (`tsconfig.web.json`) and e2e (`tsconfig.e2e.json`) |
| `test` | Vitest + Testing Library (jsdom) |
| `e2e` | Playwright for Electron, `e2e/smoke.spec.ts` |

Electron downloads its binary on first use. If `npm run dev` or `e2e` says it can't find it,
run `node node_modules/electron/install.js`. If Electron starts as plain Node ("bad option:
--remote-debugging-port"), your shell has `ELECTRON_RUN_AS_NODE=1` set (some editor
extensions export it): `env -u ELECTRON_RUN_AS_NODE npm run dev`. The e2e test strips it.

The `.dmg` is unsigned: on first open, right-click the app and choose Open.

## Stack

Electron 45, electron-vite 6 (Vite 8) for the three bundles, React 19, TypeScript 6,
TanStack Query 5 (data from main), Zustand 5 (navigation, settings, command registry),
electron-builder 26, Vitest 5 + Testing Library, Playwright 1.65. Versions are pinned
exactly in `package.json`.

## Layout

```
src/
  main/            Electron main process. index.ts (window, security), ipc.ts (handlers).
                   The data layer (DESK-003) goes here too.
  preload/         index.ts exposes the allowlisted `window.nem` API; api.d.ts types it.
  shared/ipc.ts    IPC channel type map, the channel allowlist, the NemApi interface.
  renderer/
    theme.ts       ALL design tokens (colors, fonts, type scale, layout), contrast math.
    routes.ts      The one route table (screens, hotkeys, palette keywords).
    screens/<id>/  One folder per screen; index.tsx default-exports the screen.
    shell/         Command bar, nav + splitter, palette, status bar, error boundary.
    components/    TierBadge, LoreCard, PixelIcon, PaneTitle, FlashValue,
                   EmptyState / StaleBanner / SchemaError (states.tsx).
    lib/           tier(), key handling (keys.ts), fuzzy match, value flash, api wrapper.
    state/         Zustand stores: nav, settings (persisted), commands.
    styles/        app.css (uses only CSS variables), fonts.css.
    assets/fonts/  JetBrains Mono + Silkscreen, each with its OFL license.
e2e/               Playwright Electron smoke test.
```

## Adding a screen

1. Replace `src/renderer/screens/<id>/index.tsx` (default export, no props). Read
   navigation parameters from `useNav((s) => s.params)`.
2. Only for a brand-new screen: add one entry to `src/renderer/routes.ts` (and its id to
   `ScreenId`). `hidden(flags)` hides a screen until data exists (Backtests uses
   `flags.hasBacktests`, set by the data layer with `useNav.getState().setFlags`).
3. Use the shared pieces: `<EmptyState>` (say why and which command makes data),
   `<StaleBanner newest={...}>` (15 min default), `<SchemaError file field
   expectedVersion>`, `<TierBadge verdict edgeCents>`, `<LoreCard>`, `<FlashValue>`,
   `.num` for right-aligned tabular numbers, `table.grid` for 20px rows.
4. Keys: `useKeys({ j, k, Enter, Escape, c })` from `lib/keys.ts`. The shell owns
   Cmd/Ctrl+1..8, Cmd/Ctrl+K, `/`, `r` (refresh all queries) and a fallback Escape (back).
   A screen's filter input gets `data-screen-filter` so `/` focuses it. Plain keys are
   ignored while typing in inputs. Return `false` from a handler to pass the key on.
5. Palette entries: `useRegisterCommands(useMemo(() => [...], deps))` from
   `state/commands.ts` (group "Portfolio", "Strategy", "Source", ...).

## Adding an IPC channel

1. `src/shared/ipc.ts`: add `"area:name": { request; response }` to `IpcChannels` and the
   name to `IPC_CHANNELS` (a compile-time check fails if you forget), plus a method on
   `NemApi`.
2. `src/main/ipc.ts`: add the handler to `handlers` (the mapped type makes a missing one a
   type error). Validate the request; it comes from the renderer.
3. `src/preload/index.ts`: add the method calling `invoke("area:name", req)`.
4. Renderer: call `nem().method()` from `lib/api.ts` inside a TanStack Query hook, with a
   key in `queryKeys`. `lib/api.ts` has an offline stub for tests.

Channels are read-only. There is no write, order, or key path, and there should never be.

## Security

- `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, `webviewTag: false`.
- The preload exposes only `window.nem` (allowlisted channels); main rejects IPC from any
  frame other than the bundled page (or the local dev server in dev).
- Strict CSP in the built app: `default-src 'none'`, scripts/styles/fonts from `'self'`
  only, `connect-src 'none'`, no inline code, no `data:` assets. Dev relaxes it for
  Vite's HMR on localhost only (`electron.vite.config.ts`).
- No remote content: navigation away from the app is blocked; https links open in the
  system browser; all permission requests (camera, notifications, ...) are denied.
- Fonts are bundled; nothing loads from a CDN. No secrets in this app or its config.

## Design rules

- Colors live only in `src/renderer/theme.ts` (lint fails otherwise). Text tokens are
  tested for WCAG contrast >= 4.5:1 on `--bg` and `--panel`.
- Tier labels are always shown with their color (UNDECIDED, UNDECIDED+, EDGE, NO EDGE).
- Silkscreen (pixel font) only for pane titles, the brand and tier badges, never numbers.
  Settings → "Pixel accents" off swaps it for JetBrains Mono and hides pixel icons
  without moving layout (checked in e2e).
- Value flashes last 150ms and are disabled under `prefers-reduced-motion`.
- The dev-only Kitchen Sink screen (Cmd/Ctrl+K → "Kitchen Sink" in `npm run dev`) shows
  every token, tier badge, icon and the type scale.
