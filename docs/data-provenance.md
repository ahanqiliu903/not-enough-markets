# Data provenance

Where each kind of data in NEM comes from, what it can be trusted for, and what it can't.

## Manual baseline (`nem import kalshi-pnl`)

Your own manual Kalshi trading, loaded as a read-only portfolio (`manual_baseline`,
one strategy `manual`) so NEM strategies can be compared against it.

```bash
uv run nem import kalshi-pnl data/private/realized_pnl_*.csv \
  --activity data/private/Kalshi-Recent-Activity-All.csv --from 2026-01-01 --to 2026-03-31
uv run nem summary            # the baseline appears next to the portfolio files
```

**Sources.** Both are Kalshi account exports; keep them in `data/private/` (gitignored,
never committed, never copied into tests or docs).

- *Realized-PnL CSVs*, one row per closed lot: side, quantity (can be fractional), entry
  and exit price, open and close fees, P&L with and without fees, open and close time.
  This is the truth for money: P&L is stored as Kalshi reports it, never recomputed.
  Validation only checks that the row is self-consistent (P&L without fees = quantity x
  (exit - entry), P&L with fees = that minus fees, to the cent).
- *Activity CSV* (optional), one row per fill: exact fill time and maker/taker. Quirks:
  `Price_In_Cents` on No rows is the Yes price (paid = 100 - price), `Amount_In_Dollars`
  is a contract count, there is no buy/sell field, and Result/Profit are empty. Only
  `type=Trade` rows are fills; fills are kept for markets that have an imported lot.
  They are stored (`manual_fills`) for later tickets; nothing is computed from them yet.

**What is kept.** Excluded by default: `type=credit` rows (referral credits, not
trading) and markets whose ticker doesn't start with `KXHIGH` (`--include-non-weather`
keeps them). Window: `--from` / `--to` (optional, every lot by default), inclusive, on
each lot's **close date in the timestamp's own UTC offset** (Kalshi writes US Eastern). A
lot closing at 23:30 ET on the last day is in, although it is the next day in UTC; one
closing at 23:30 ET the day before the start is out.

**Validation.** Every row is checked (pydantic) before anything is written. Any malformed
row fails the whole import, listing every error by file and line number.

**Idempotency.** A lot's key is a hash of its parsed fields (ticker, side, quantity,
prices, fees, P&L, open and close time) plus its occurrence index among identical rows,
counted in file order over all rows before filtering. Identical-looking rows are real
separate lots and stay separate; importing the same files again adds nothing; renaming a
file or changing the window doesn't change keys. Re-imports only add: the portfolio is
the union of everything imported into it.

**Storage.** Tables `manual_lots` and `manual_fills` (SQLite schema v4), every row
`provenance = manual_import`. They are separate from the engine's `trades`: the engine
never reads or trades them, and there is no portfolio file for them.

**How it is reported.**

- Header: lots, entry notional (entry price x contracts), P&L after fees, fees, exits at
  0.99 vs every other exit (sells below 99c such as stop-outs, settlements at 1.00 / 0.00).
- Per lot, unweighted: each lot counts once whatever its size. Win = lot P&L after fees
  > 0. Win rate with a 95% Wilson interval, the mean entry price, and the edge = mean of
  each lot's P&L after fees per contract, in cents, with a normal 95% interval.
- By month of close (US Eastern): lots, contracts, notional, P&L, edge per contract.
- Then the engine's usual strategy statistics, unchanged (break-even = mean entry + fees
  per contract, SPRT verdict). They assume a hold-to-settlement payout, so with early exits
  (a stop-out loses less than its whole cost) they misstate the edge; use the per-lot
  P&L-based edge for money.
- Held to settlement (counterfactual, after `nem settle verify`): each lot re-scored as a
  $1/$0 bet on its market's checked result: `derived_result` whenever the check has one
  (`agree`, `disagree`, or `kalshi_missing`, e.g. a market Kalshi settled `scalar`, at a
  price), as `nem settle verify` replays it; else Kalshi's yes/no; lots with no check, or
  with neither a derived value nor a yes/no from Kalshi, are excluded and counted. Cost = entry + the lot's actual open fee,
  no close fee (Kalshi charges none at settlement). Same statistics and verdict wording as
  the engine; each lot is one trade, so a fractional quantity changes only its cost per
  contract and its dollars. Also: P&L held vs the actual exits, by exit kind. Lots on the
  same market share one outcome and are not independent, so the per-lot z, Wilson
  interval and sequential test overstate the evidence; the report also shows the number of
  distinct markets, how many had a losing lot, and the per-market win rate (a market is won
  when none of its lots lost) with a Wilson 95% interval. JSON: optional
  `held_to_settlement` on the strategy row (contract/CHANGELOG.md).
- In `nem summary --json` (and so NEM Desk) it is a `live` portfolio (it was real money)
  with a `retired` strategy. Contract 1.0 has no field for "imported", so
  `starting_balance_usd` is the entry notional, a placeholder so the row validates:
  `equity_usd` and `return_rate` are **not** an account balance or bankroll.

**Limits.** Manual position sizes usually change over time (deposits, credits, sizing
up), so the dollar P&L curve reflects changing size as much as edge. Read the per-lot and
per-month numbers for edge. NEM does not model the bankroll or infer sizing rules from
this data. Kalshi's reported settlement is taken as given here; checking it against
station readings is the next section.

## Settlement truth (`nem settle verify`)

Kalshi's reported result for a daily temperature market is checked against an
independent reading for the station its rules name, and the winning side is recomputed
from the market's own strike definition.

```bash
uv run nem settle verify --from-pnl data/private/realized_pnl_*.csv   # or --tickers FILE
# default without either: the weather tickers in the db's settlements and manual_lots
```

**Which answer wins.** Money questions (what was actually made or lost): the
realized-PnL export, always. Strategy replay (what a strategy should have scored):
`derived_result`. Every market where they differ, either side is missing, or a caveat
applies goes to `reports/settlement_disagreements.csv` (gitignored) with the realized P&L
next to it when `--from-pnl` is given. Nothing is resolved silently. Replay still reads
the engine's `settlements` table; using `derived_result` there is a later ticket.

**Station mapping.** `src/nem/settle/stations.py`, with where each value came from (CLI id
from each series' market rules, ICAO / office / name from IEM's CLI archive, ASOS network
from IEM). Tested against `docs/kalshi-tickers.txt`. Series that aren't mapped are
reported `source_missing` ("series not in nem.settle.stations"); `nem series uncovered`
lists them.

**Settlement source changed over time; read it per market.** Rules fetched 2026-11-21 for
every market checked: markets through about July 2026 settle on the **NWS Daily Climate
Report (CLI)** ("as reported by the National Weather Service's Climatological Report
(Daily)"); later ones (switch dates vary by series, mid-August to September 2026) on **The
Weather Company** (weather.com/kalshi, "(CLINYC)" etc.). None named Weather Underground.
`rules_source` records what each market's rules say; for Weather Company markets the CLI
reading is a cross-check, not the settlement source. Old settled markets are only served
by Kalshi's `/historical/markets/{ticker}` (the live endpoint answers 404).

**Independent sources** (Iowa Environmental Mesonet: public domain, "may be used freely by
anyone for any lawful purpose", no warranty, attribution appreciated; underlying NWS
products are US government works):

1. `iem_cli`: IEM's parse of the CLI (`/json/cli.py?station=KNYC&year=2026`), one request
   per station-year; `source_ref` is the CLI product id (raw text at
   `/api/1/nwstext/<id>`, corrections included). Preferred: it is the official record and
   what the older rules settle on.
2. `iem_asos_obs` (fallback, `approximate`): highest / lowest METAR temperature inside the
   climate day (`/cgi-bin/request/asos.py`, rate limited per IP; we pause 1 s between
   requests and back off on 429/503). On a sample of 12 winter days it matched the CLI
   high 9 times and was 1-2F low otherwise: METARs can miss the peak, so for highs it is a
   lower bound. A reading within 1F of a strike is flagged "not conclusive". IEM's own
   ASOS daily summary is not used: its day definition isn't documented and it differed
   from the CLI by 1F on a day checked.
3. Weather Underground / The Weather Company: manual cross-check only. Their terms forbid
   bots and scrapers without written permission, and there is no free station-history
   API. Requests use a generic User-Agent with no contact details.

**Rules for deriving the result** (`src/nem/settle/rules.py`, unit-tested):

- *Climate day*: midnight to midnight local **standard** time (CLI tables say "TIME
  (LST)"). In daylight saving time that is 01:00 to 00:59 clock time, so 00:30 EDT on
  Jul 2 counts for Jul 1. Always 24 hours, also on the days clocks change; Phoenix has
  no shift. Kalshi stops trading at 11:59 PM, so in summer up to an hour of the climate
  day is still to come at close (matters most for lows).
- *Whole degrees*: CLI values are whole F. Readings with decimals are rounded half up
  (16.5 -> 17, -0.5 -> 0) before comparing.
- *Strikes*: from Kalshi's `strike_type` / `floor_strike` / `cap_strike`, never the ticker
  letter. `T` is `less` (cap = the number: T53 is YES at 52 or below) or `greater` (floor
  = the number: T30 is YES at 31 or above); `B` is `between`, floor = ticker - 0.5, cap =
  ticker + 0.5, both inclusive (B16.5 is 16 or 17).

**Status** (`settlement_checks.status`, schema v5): `agree` / `disagree` (derived vs
Kalshi's yes/no); `kalshi_missing` (Kalshi has no yes/no result: unfetched, unsettled, or
`scalar`, i.e. settled at a price); `source_missing` (no independent reading or no usable
strike). `note` lists every caveat; `kalshi_value` (Kalshi's own reading) is compared with
the observed value too. The table is `settlement_checks` because `settlements` already
holds the engine's Kalshi outcomes.

**Not verified.** Which CLI version IEM keeps when an office issues several (we store the
product id it gives); the rounding direction for negative half degrees; that The Weather
Company's value always equals the CLI for newer markets.

## Forecast backfill (`nem backfill forecasts`, `nem backfill coverage`)

Archived model forecasts fetched **after the fact**, so the forecast gates
(`source_agreement`, `bracket_distance`, `sky_conditions`) can be replayed on past
periods. This is an approximation of what was knowable, **not** what a trader saw on screen
(weather apps, NWS point forecasts and The Weather Company's forecasts are not archived).

```bash
uv run nem backfill forecasts --from-db data/nem.db --cache /some/scratch/dir  # or --tickers FILE
uv run nem backfill coverage --report reports/forecast_coverage.csv            # gitignored
```

**Sources** (checked 2026-11-21; `src/nem/backfill/sources.py`). Default: the first four.

| source | what | runs | archive | issue time |
|---|---|---|---|---|
| `iem_mos_gfs` | GFS MOS (MAV) text as NWS issued it, station-specific | 00/06/12/18Z | IEM, since 2003 | cycle explicit |
| `iem_mos_nam` | NAM MOS (MET) text | 00/12Z | IEM, 2008 to 2026-11-03 (NAM retired) | cycle explicit |
| `iem_mos_nbs` | National Blend of Models text (NBS) | 01/07/13/19Z, 00/06/12/18Z from 2026-05-05 | IEM, since 2018 | cycle explicit |
| `openmeteo_ecmwf_ifs` | raw ECMWF IFS run, Open-Meteo's 9 km grid, hourly | 00/06/12/18Z | single-runs API, since 2024-03-14 | `run=` explicit |
| `openmeteo_gfs_global` | raw GFS run (opt-in: same model as the GFS MOS) | 00/06/12/18Z | single-runs API, since 2026-04-02 | `run=` explicit |
| `openmeteo_gem_global` | raw Canadian GEM global run (opt-in) | 00/12Z | single-runs API, since 2026-05-28 | `run=` explicit |
| `openmeteo_icon_global` | raw DWD ICON global run (opt-in) | 00/06/12/18Z | single-runs API, since 2026-04-02 | `run=` explicit |

- *IEM MOS archive* (`/cgi-bin/request/mos.py?station=KNYC&model=GFS&sts=..&ets=..&format=csv`):
  public domain like the rest of IEM (see Settlement truth), rate limited per IP (HTTP 429 after
  about one request a second); we pause 3 s and back off. The text is what NWS
  disseminated, with no later revisions, which makes it the most faithful source here.
- *Open-Meteo single-runs API*: one archived run chosen by its initialization time. Free
  tier is CC BY 4.0, non-commercial only, 600/min, 5,000/h, 10,000/day. Whether a private
  trading-research tool counts as non-commercial is not stated in their terms; ask them
  (info@open-meteo.com) before relying on it, or use the paid tier. Attribution: "Weather
  data by Open-Meteo.com". Its grid point can sit kilometres from the station (Central
  Park maps to a point across the Hudson) and values are raw model output with no station
  bias correction; hourly instantaneous temperatures understate the true max slightly.
- *Not used.* Open-Meteo's historical-forecast API (it stitches the first hours of each run
  into a continuous series: closer to an analysis than to a forecast as of a time) and its
  previous-runs API (`_previous_dayN` = "predicted N x 24 h before the valid time"; the run
  that produced each hour is not exposed, so the issue time can't be recovered exactly).
  NOAA NBM/GFS GRIB2 on public cloud buckets: faithful but gigabytes per cycle, and IEM's
  text products already carry the NBM and GFS MOS station values.
- The sources are not independent: NBM blends GFS, ECMWF and others, and the GFS MOS and
  raw GFS share a model. "3 sources agree" from this set is weaker evidence than three
  independent forecasters.

**`known_at`** = model run time + a fixed lag per source (`SOURCES` in
`src/nem/backfill/model.py`): GFS MOS 5 h, NAM MOS 4 h, NBM text 2 h after IEM's run hour,
ECMWF IFS 8 h, GFS raw 6 h, GEM 8 h, ICON 6 h. These are **assumptions, set later than usual availability**,
not measurements: the archives keep the cycle, not the minute a product went public (IEM's
text-product timestamps are the cycle time too). A row can only look staler than it was.
It is never the target date and never the fetch time.

**Fields per station and CLI climate day** (local standard time, as in Settlement truth), keyed like
live forecast feeds (`YYYY-MM-DD/high`):

- `high` / `low`, F. MOS: the X/N (TXN for NBM) at the evening hour is the daytime max
  (07-19 LST), at the morning hour the overnight min (19-08 LST). That is not exactly the
  CLI's midnight-to-midnight day, which matters for lows and for days where the high comes
  at night (fronts). Open-Meteo: max / min of the 24 hourly values of the climate day.
- `cloud`: mean sky cover % over 07-19 LST. MOS categories map to okta midpoints (CL 0,
  FW 19, SC 44, BK 75, OV 100); NBM and Open-Meteo give %.
- `pop`: max 6-h precipitation probability over the periods overlapping 07-19 LST (none
  from ECMWF IFS, which is deterministic).
- `qpf_in` (NBM, Open-Meteo, inches) or `qpf_cat` (GFS/NAM MOS category 0-6) over the
  same periods.

**Horizon rules** (`horizon_errors`, applied to every row before it is stored; violators are
dropped and listed): provenance is `backfill`; `known_at = run_time + lag`; the run started
no later than the window the value summarizes (`valid_from`; no nowcasts of a day already
under way, so e.g. the 13Z NBM run never supplies that day's high); `valid_from` is inside
the climate day or its evening before; `known_at` is before the climate day ends; `known_at`
is not after the fetch. Partial windows (a run that ends mid-day) are not stored.

**No look-ahead.** Consumers select with `known_before(rows, at)`: for a decision time (a
trade's open), the newest row per source and field with `known_at <= at`; it re-checks its
own output and raises `LookaheadError` otherwise. Tested with a later-issued run that must
be excluded.

**Storage.** Table `forecast_backfill` (SQLite schema v6; NEM Desk accepts v3-v7 and reads
nothing from it), every row `provenance = 'backfill'` (enforced by a CHECK), with `source`,
`run_id` (e.g. `GFS MOS 2026-01-23T12Z`), `run_time`, `valid_from`, `known_at`,
`fetched_at`. Deliberately **not** `feed_values`: the engine replays `feed_values` as
forward-recorded data with no provenance, and backfill must never pass for that.
Contract 1.0 is unchanged.

**Reporting.** Anything driven by backfilled forecasts is labeled "backfilled forecasts"
(`nem.stats.provenance.label`) and its verdict goes through `cap_verdict`: "edge" becomes
"undecided" unless forward-recorded data also says "edge"; "no_edge" stands (a backtest
can reject, not confirm). The forecast gates (`src/nem/gates/forecast.py`)
read backfill when a gate sets `backfill_sources`, through `ctx.forecasts`, which applies
`known_before` at the decision time and drops runs older than `backfill_max_age` (48 h);
each value carries `provenance` (`feed` / `backfill`) and its `run_id`.
`nem.stats.provenance.uses_backfill(strategy)` says whether a strategy's verdict must be
capped (wiring it into `nem summary` is not done yet).

**Coverage report** (`reports/forecast_coverage.csv`, one row per imported manual entry:
ticker and open minute): the sources with a forecast of the market's measure known before the entry,
each value with its run and `known_at`, min / max / spread / median, what the median implies
under the market's strike (from `settlement_checks`, run `nem settle verify` first), the
observed value, sky fields, and `lt3_sources` = fewer than 3 sources (can't test
`source_agreement`).

## Live model forecast feeds (`iem_mos`, `open_meteo_run`)

Forecast feeds recorded forward by `nem run`, so the forecast gates (`source_agreement`
with `min_sources: 3`, `bracket_distance`, `sky_conditions`) work in forward paper trading
without backfill. Code: `src/nem/feeds/model_forecast.py`; example:
`examples/weather/forecast_gates.yaml`. Checked 2026-11-21.

| feed | model | route | runs | lag (`known_at`) | terms |
|---|---|---|---|---|---|
| `iem_mos`, `model: GFS` | GFS MOS (MAV), station-specific statistical guidance | IEM `mos.py` CSV | 00/06/12/18Z | run + 5 h | public domain |
| `iem_mos`, `model: NBS` | National Blend of Models station text | IEM `mos.py` CSV | 00/06/12/18Z | run + 2 h | public domain |
| `nws_forecast` (existing) | NWS point forecast (NDFD gridpoints) | api.weather.gov | as updated | product `updateTime` | public domain |
| `open_meteo_run`, `model: ecmwf_ifs` | raw ECMWF IFS, 9 km | Open-Meteo single-runs | 00/06/12/18Z | run + 8 h | free tier non-commercial only (opt-in) |
| `open_meteo_run`, `model: gem_global` | raw Canadian GEM global (CMC) | Open-Meteo single-runs | 00/12Z | run + 8 h | same (opt-in) |
| `open_meteo_run`, `model: icon_global` | raw DWD ICON global | Open-Meteo single-runs | 00/06/12/18Z | run + 6 h | same (opt-in) |
| `open_meteo_run`, `model: gfs_global` | raw GFS (same model as GFS MOS) | Open-Meteo single-runs | 00/06/12/18Z | run + 6 h | same (opt-in) |

**Independence.** Three families of models: NOAA's GFS, ECMWF's IFS, and Canada's GEM
(DWD's ICON is a fourth). Of the public-domain feeds only GFS MOS is a single model: the
NBM blends GFS, ECMWF, GEM, ICON and others, and the NWS point forecast starts from NBM
grids edited by forecasters. So GFS MOS + NBM + NWS gives three values but well under three
independent opinions (NBM and NWS often agree closely). The most independent free set is
**GFS MOS + ECMWF IFS + GEM** (optionally plus NBM or NWS), which needs the Open-Meteo
feeds. Raw GFS on Open-Meteo adds nothing independent of GFS MOS. NAM MOS is gone (NAM
retired 2026-11-03).

**Terms** (read the pages; not legal advice):

- *IEM*: "The materials found on this website are in the public domain and may be used
  freely by anyone for any lawful purpose"; attribution appreciated
  (https://mesonet.agron.iastate.edu/disclaimer.php). No published rate limit; it answers
  HTTP 429 above about one request a second per IP (see Forecast backfill), so feeds keep 2 s between
  requests to it.
- *NWS / api.weather.gov*: US government work, public domain; asks for a User-Agent with
  contact details (https://www.weather.gov/documentation/services-web-api).
- *Open-Meteo free API* (https://open-meteo.com/en/terms): "You may only use the free API
  services for non-commercial purposes." Commercial examples: subscription or ad-funded
  sites or apps, commercial products, and "undisclosed research at commercial entities".
  Limits: fewer than 10,000 calls a day, 5,000 an hour, 600 a minute (300,000 a month),
  per IP and application. Data are CC BY 4.0 (https://open-meteo.com/en/licence):
  credit "Weather data by Open-Meteo.com" in anything that shows the data. A private
  research tool that paper-trades is not in their list of commercial uses, but trading for
  profit is not clearly non-commercial either; ask them (info@open-meteo.com) or use a paid
  plan before relying on it. That is why the feed requires `terms: non-commercial` in its
  config and the example leaves it commented out. Underlying model licences: ECMWF open data
  CC BY 4.0, commercial use allowed (https://www.ecmwf.int/en/forecasts/datasets/open-data);
  ECCC/MSC data commercial use allowed with attribution
  (https://eccc-msc.github.io/open-data/licence/readme_en/); DWD CC BY.
- *Not used*: ECMWF open data and MSC Datamart directly (free, commercial use allowed, but
  GRIB2 grids only: needs eccodes and downloads of tens of MB per run); NAM MOS (retired).
  These are the routes to replace Open-Meteo if its terms don't fit.

**One value per model.** Every feed and backfill source has a model identity
(`SourceInfo.model` in `src/nem/backfill/model.py`; `Feed.forecast_model` for feeds):
`gfs` (GFS MOS feed, `iem_mos_gfs`, raw GFS on Open-Meteo), `nbm` (NBS feed,
`iem_mos_nbs`), `nam`, `ecmwf_ifs`, `gem`, `icon`. `nws_forecast` and feeds that declare no
model each count as their own source. `ctx.forecasts` keeps one forecast per model
(`one_per_model`): the recorded feed value if one is fresh and known at the decision time,
else the newest backfill row; each kept value keeps its own `provenance` (`feed` /
`backfill`). So `min_sources: 3` means three models, never one model reached two ways.
Strategy-level verdict capping is unchanged: any strategy that sets `backfill_sources` is
capped, even when feeds supplied every kept value.

**Same meaning as the backfill.** Both feeds use the backfill's query builders
(`mos_params`, `open_meteo_params`), parsers (`parse_mos_csv`, `parse_open_meteo`),
station coordinates and `SOURCES` lags, and drop anything that fails the backfill's
horizon rules (`horizon_errors`). Keys are `YYYY-MM-DD/{field}` with the fields, units and
windows of the forecast backfill (`high`, `low`, `cloud`, `pop`, `qpf_in`, `qpf_cat`), so a live value
from a run equals the backfilled value of the same run (tested). Differences: the live feeds
read the run once the lag has passed; a backfill sees the archive's (identical) copy later.
`nws_forecast` is not in the backfill and its `high` is the daytime period temperature, its
`low` the preceding night's (no midnight-to-midnight climate day).

**`known_at`** = run time + the source's lag, and a run is emitted only after that time has
passed, even if the server already has it, so a live value is never fresher than the
backfill would make it. On 2026-11-21 the 12Z runs were public about 6.5 h (ECMWF), 5.8 h
(GEM) and 4 h (ICON) after the cycle on Open-Meteo (`/data/<model>/static/meta.json`,
`last_run_availability_time`), and the 18Z NBS was on IEM within 1.2 h.

**Fetching.** `every: 30m` (forecasts change a few times a day). A fetch makes no request
when the newest releasable run was already read. Each fetch that does request reads every
qualifying run of the last `lookback` (30 h): one IEM request per feed, one Open-Meteo
request per unread run (a run the API doesn't have yet answers HTTP 400 and is asked again
next time). So a feed started mid-day still records the morning runs that forecast today's
high (runs started after the window they summarize never do; a day's Open-Meteo `high`
comes from runs before local midnight, a MOS `high` from runs up to 07 LST), and steady
state is about 4 requests a day per feed (2 for GEM). Network errors, 429 and 5xx are
retried twice with backoff (or `Retry-After`, capped at 60 s); after that the fetch fails,
is logged, and is retried on the next `every`. Because the gate keeps the newest value per
source and a day's last eligible run can be many hours old by the evening, use
`max_age: 24h` on these feeds.

**User-Agent.** `user_agent` in the feed config, else `$NEM_USER_AGENT`
(deploy/nem.env.example), else a generic one. Put your contact in the environment, not in a
portfolio file; the same applies to `nws_forecast`.

## Kalshi history (`nem import kalshi-history`, `nem history`)

Settled markets from Kalshi's public historical API (no key), as replay data for periods
NEM never recorded. Stored in a **separate file** (default
`data/private/kalshi_history.db`, gitignored), never in `data/nem.db`: the import refuses a
file that holds recorded snapshots, and `nem replay` refuses a history file.

```bash
uv run nem import kalshi-history --series KXHIGHNY --from 2026-01-01 --to 2026-04-30
uv run nem import kalshi-history --series KXHIGHAUS --strike-type between ...  # some brackets only
uv run nem backfill forecasts --tickers FILE --db data/private/kalshi_history.db  # forecast gates
uv run nem history replay --dir DIR --from 2026-01-01 --to 2026-02-28
uv run nem history suggest PORTFOLIO --dir DIR --grid gates.1.max_spread=null,4,6 \
  --train-from 2026-01-01 --train-to 2026-02-28 --test-from 2026-03-01 --test-to 2026-04-30
```

**Sources** (docs.kalshi.com, checked 2026-11-21; `src/nem/history/fetch.py`):
`/historical/markets?series_ticker=` (paginated; no close-time filter, so the window is
applied by the date in the event ticker), `/historical/markets/{ticker}/candlesticks?
period_interval=1` (one candle per minute, OHLC of the best YES bid and ask, at most 5,000
per request), `/historical/trades?ticker=` (prints, paginated). Raw responses are cached
(`--cache`, default `data/private/cache/kalshi/`), requests are paced (`--pause`, 0.25 s)
and retried with backoff on 429 / 5xx. Markets already imported are skipped, so an
interrupted import resumes.

**What is stored.** `snapshots`: the closing best bid and ask of each minute candle,
stamped at the candle's end, **only when they changed** from the minute before (no depth,
no sizes); NO quotes are the YES quotes mirrored. `settlements`: Kalshi's archived yes/no
result (other results are kept in `history_markets.result` and can't be settled).
`history_markets` (one row per market, provenance `historical_candles`, written last so a
row means complete) and `trade_prints` (every archived print) are created by
`nem.history.db` in history files only; the engine schema and version are unchanged.

**How it replays** (`nem history`, `HistorySource`): one tick per minute with every open
market's latest quote, markets chosen by market date so a train and a test period never
share a market. Entries fill in full at the minute's ask (no depth: size is unknown, so cap
contracts with `fixed` sizing). Stops sell at the minute's bid. Take-profit: `pessimistic`
never fills at 99c, `realistic` / `optimistic` read resting bids (here only the top of
book), `trade_through` counts prints (upper bound, see docs/exits-and-fills.md). Fees must be
set in the portfolio file (`taker_fee_rate`, `maker_fee_rate`): nothing is looked up.

**Reporting.** Every result is labeled "historical candles, no depth" (and "backfilled
forecasts" when a forecast gate reads backfill) and every verdict goes through
`cap_verdict`: a backtest here can reject an idea, never confirm it. Statistics are per
trade (the engine trades at most one market per strategy per event) and per market date
(cities on one date share the weather and are not independent).

**Limits.** No depth and no queue: entry size and stop proceeds are optimistic for size.
A candle's close is one instant per minute; quotes inside the minute are not seen. Kalshi's
archived result is taken as given (check with `nem settle verify`). Series created
mid-period (e.g. a city added in February) simply have fewer markets.
