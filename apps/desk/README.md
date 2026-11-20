# NEM Desk

A read-only desktop app for looking at NotEnoughMarkets results: portfolios, strategy
verdicts, trades, market data and engine status. It reads files the engine exports and
never computes statistics, places orders, or holds Kalshi keys.

Status: shell (DESK-001/002/005), folder data layer and sources with file watching
(DESK-003/004), and the Portfolios, Strategy, Trades and Compare screens (DESK-006..009).
Market, Backtests and Status still show empty states.

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
Ajv 8 + csv-parse 7 (main-process readers), TanStack Table 8 + Virtual 3 (trades blotter),
uPlot 1.6 (P&L curves), electron-builder 26, Vitest 5 + Testing Library, Playwright 1.65.
Versions are pinned
exactly in `package.json`.

## Layout

```
src/
  main/            Electron main process. index.ts (window, security), ipc.ts (handlers).
                   data/ is the data layer: CSV/JSON readers validated against
                   contract/schema (bundled at build time), sources + watching.
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

## Explore (Kalshi markets, screen 9)

Cmd/Ctrl+9, or type `explore SERIES` in the command bar. Read-only research on Kalshi's
**public** market data: no key, no account, no orders.

- **Client in main only** (`src/main/kalshi/`): GET-only, host allowlist
  `https://api.elections.kalshi.com/trade-api/v2/` (anything else is a `blocked` error),
  `User-Agent: NEM Desk/<version>`, no auth headers, redirects refused. At most 5 requests/s,
  retries on 429 / 5xx / network errors with 0.5s, 1s, 2s backoff (Retry-After honored, capped
  at 10s). Caches: markets, market, order book 5s; series 10 min; the full `/series` list
  (used by search, ~20 MB) 1 h. Parsing mirrors `src/nem/market/kalshi.py` (dollar strings,
  0 bid / 1 ask = none, order book bids best-first; asks are the other side's bids at 1 - p).
- **Channels** `kalshi:searchSeries | series | markets | market | orderbook |
  copyStrategyCommand`, exposed as `window.nem.kalshi.*`. Errors are values
  (`{ ok: false, error: { kind } }`, kinds `offline`, `rate_limited`, `upstream`,
  `not_found`, `bad_request`, `invalid_response`, `blocked`) and render as states. The
  renderer CSP stays `connect-src 'none'`.
- **"Start a strategy here"** asks main to put `uv run nem strategy add PORTFOLIO NAME --series S`
  on the clipboard (Electron `clipboard`), so the renderer keeps every permission denied.
- **Categories** are a hand-written seed (`screens/explore/catalog.ts`, from
  `docs/kalshi-tickers.txt`); the Search tab reaches any series.
- **Test switch:** `NEM_DESK_KALSHI_FIXTURES=<dir>` (unpackaged builds only) serves recorded
  responses from `<dir>` instead of the network. `e2e/explore.spec.ts` sets it to
  `e2e/fixtures/kalshi/` (recorded 2026-11-20; file names are listed in
  `src/main/kalshi/fixtures.ts`). Unit tests also parse the engine's
  `tests/fixtures/kalshi/*.json`. No test touches the network.

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

## Data sources

A source is a folder of engine output: an `out/` folder or its parent (e.g. `~/nem-cache`
holding an rsynced `out/` plus optional `summary.json` / `status.json`). Add one in Sources
and Settings (Cmd/Ctrl+8) or from any empty state. The list lives in the app's userData
folder (`sources.json`); source folders are only read.

- **Reading** (`src/main/data/`): `csv.ts` coerces cells by the rules in
  `tests/contract/test_contract.py` and validates each row with Ajv against
  `contract/schema/*.json`; any other `schema_version` major, a missing column, an invalid
  row or duplicate rows block that file, and the problem comes back as a `DataError`
  (file, row, field, message, expected/found version) that screens show with
  `<SchemaError>`. Nothing is thrown to the renderer.
- **Trades** stay in main: `data:trades` filters, sorts and pages them (the sorted index is
  cached, so scrolling is a slice); `data:tradesCsv` returns the matching rows with the
  engine's own cell text for export; `data:curves` gives cumulative P&L for charts.
- **Watching**: a recursive `fs.watch` plus a 1 s mtime/size poll; changes reach the
  renderer as a `data:changed` push and every data query refetches (under 2 s).
- **Sample data**: in dev, "Load sample data" points at `contract/fixtures/basic`. Packaged
  builds bundle a copy of `basic/out`, `summary.json` and `status.json` as an
  extraResource (`<resources>/sample`, see `electron-builder.yml`); `nem.db` is left out.
- e2e runs against a throwaway profile via `NEM_DESK_USER_DATA` (ignored when packaged).
