# Exits and fill models

By default a position is held to settlement. A strategy can instead exit early with a
resting take-profit sell, a stop, or both. Paper only: the same rules apply in `nem run`,
`nem replay` and `nem suggest`.

```yaml
strategies:
  - name: example
    series: KXEXAMPLE
    # ... signal, gates, sizing, risk ...
    exit:                      # omit (or null) to hold to settlement
      take_profit:
        price: 0.97            # whole cents, 0.01-0.99
        fill_model: realistic  # pessimistic | realistic | optimistic (required)
        maker_fee_rate: null   # default: from the series' fee schedule (see Fees below)
      stop:
        bid_at_or_below: 0.60  # whole cents, > 0; must be below take_profit.price
```

Code: `src/nem/execution/exits.py` (fill rules), `src/nem/engine/exits.py` (when they run),
`src/nem/stats/exits.py` (P&L split by exit kind).

## What the recorded data can show

Each poll records the top of book and the resting bids per side with sizes (`depth`).
There are **no trade prints, no queue positions, and nothing between polls**, and our own
paper orders are never in the recorded book. Every fill rule below is an inference from
resting bids at poll times. Prices are for the held side: holding NO, "bid" is the NO bid
and "ask" is 1 minus the best YES bid.

## When exits run

- Every tick, on that tick's snapshots only (book time <= now), for active, paused and
  halted strategies (a halt stops new entries, not exits). Retired strategies don't run.
- Only on books recorded **after** the position opened: the entry book can't fill an exit.
- Only while the market is open; after close, settlement decides.
- All-or-nothing: the whole position leaves in one fill, or nothing fills this poll and
  the exit tries again next poll. Partial exits are not modelled.
- With no recorded depth, the top-of-book bid is assumed to cover the whole position (the
  entry broker makes the same assumption for the ask). Record with depth.

## Take-profit fill models

A resting sell of the whole position at `price`, placed when the position opens. It fills
at exactly `price` (no price improvement) and pays the maker fee,
`round_up_to_cent(maker_fee_rate * contracts * price * (1 - price))`.

### Fees

Kalshi publishes how each series is charged at `GET /trade-api/v2/series/{ticker}`
(public): `fee_type` and `fee_multiplier`. Rates left null in the portfolio file follow it
(`src/nem/market/fees.py`); a rate you set always wins and is never looked up.

| `fee_type` | `maker_fee_rate: null` becomes | `taker_fee_rate: null` becomes |
|---|---|---|
| `quadratic` | 0 (takers only) | 0.07 x `fee_multiplier` |
| `quadratic_with_maker_fees` | 0.0175 x `fee_multiplier` | 0.07 x `fee_multiplier` |
| `flat`, anything else | error: set it | error: set it |

- `nem run` looks up every non-retired strategy with a null rate when it starts (and on
  a reloaded edit). If the series can't be read or its `fee_type` isn't modelled, it
  refuses to start (an edit is rejected and the old config keeps running).
- `nem validate` looks up only null maker rates, so portfolios without a take-profit
  validate offline.
- `nem replay`, `nem suggest` and `nem demo` never touch the network, and series metadata
  isn't recorded in the store: a take-profit with a null maker rate stops them with an
  error (set the rate; 0 on a `quadratic` series), and a null taker rate means 0.07.

As of 2026-11 the KXHIGH* daily weather series report `quadratic`, multiplier 1: no maker
fee. Kalshi changes this; check before trusting an old setting.

| Model | Counts as filled at the first later poll where | Reading |
|---|---|---|
| `optimistic` | best ask >= `price` (our order would be the best offer) or best bid >= `price` | Assumes we're first in the queue and a buyer arrives before the next poll. An upper bound, not evidence of a trade. |
| `realistic` | resting bids at >= `price` add up to the whole position | A buyer can only rest at or above our ask once every sell at or below it was taken, so our order would have been taken too, from that leftover size. Misses buyers who came and went between polls. |
| `pessimistic` | resting bids strictly above `price` add up to the whole position | The book traded through our level. **At 99c this can never happen** (99c is Kalshi's top price), so a 99c take-profit never fills and the position is held to settlement. |
| `trade_through` | trade prints of the held side at or above `price`, after the order was placed and up to this poll, add up to the whole position | **Historical replay only** (`nem history`, trade prints from Kalshi's archive). An upper bound: it assumes every such print would have hit our order first, ignoring the queue at our price. A source without prints refuses to run it (`nem run`, `nem replay`). |

Compare models before trusting a result: if a strategy only works under `optimistic`, it
depends on fills the data can't confirm.

## Stop

Once the best bid is at or below `bid_at_or_below`, sell at market: walk the recorded bids
best first, taker fee per level (`taker_fee_rate` of the strategy). If the bids can't absorb
the whole position, or there are no bids, nothing fills and the stop retries next poll.
The take-profit is checked before the stop on the same poll.

## How exits are recorded

An exit writes an `orders` row with status `exit_take_profit` or
`exit_stop` (deterministic id, `nem.execution.exits.exit_order_id`), a `fills` row, and
settles the trade at the exit time with `realized_pnl` = proceeds - exit fee - cost (entry
price and entry fee) and `won = realized_pnl > 0`.

In the same transaction it queues the trade in `exit_learning` (schema v7). Learned win
counts (`ctx.stat`, used by `extreme_favorite`) and the signal's `on_settle` mean "this
side won at settlement", which isn't known when the position is sold early, so they wait:
when the market later settles (the same settlement pass that settles held trades, with
the result known at that tick, so no look-ahead), the side's count is bumped with whether
it **won at settlement** and `on_settle` is called, exactly once per trade, as for a held
trade. The trade handed to `on_settle` has `won` = the settlement outcome but keeps the
exit's `realized_pnl` and `settled_at`. The queue lives in the database, so it survives
restarts; upgrading to v7 queues exits recorded before it.

So for a strategy with exits there are two win counts: the report's win rate and
`trades.won` are P&L > 0 (money), while learning uses the settlement outcome. `nem summary`
shows both per exit kind (`by exit` line: trades, P&L, how many had P&L > 0, how many
sides won at settlement, how many are still pending), from
`nem.stats.exits.by_exit_kind`; `nem summary --json` adds an optional `exits` object to the
strategy row.

## Mark-to-market (unrealized P&L)

Reports (`nem summary`, `nem report`) value every open position as if it were sold now, at
market (`nem.execution.mark`, contract 1.1):

- **Liquidation value**: the latest recorded snapshot of the position's market at the
  report time, its resting bids on the position's own side walked best first for the
  position's contracts, taker fee per level at the strategy's `taker_fee_rate` (the same
  walk as a stop, `walk_bids`). Reports don't touch the network, so a null
  `taker_fee_rate` means 0.07 here, as in replay.
- **No bids = $0**: if the recorded bids cover fewer contracts than held, the covered ones
  (`mark_contracts`) are valued at the walk and the rest at $0: nobody would buy them now
  (e.g. a market all but resolved against us), so counting them at a guessed price would
  overstate the position. An empty bid side means `mark_value_usd` 0, `mark_bid_cents`
  empty (no price) and unrealized P&L = -cost. `mark_contracts` < `contracts` flags it; it
  is a pending loss that bids returning (or a winning settlement) can undo. A snapshot
  that recorded no depth for that side at all (top of book only) is unknown rather than
  empty, so the trade is left unmarked instead.
- **Unrealized P&L** = liquidation value - entry cost (price x contracts + entry fee),
  always set when the trade is marked.
- **Mid** ((best bid + best ask) / 2 on the held side, best ask = 1 - the other side's best
  bid) is reported as a reference only.
- **No mark** (all mark columns empty) when the trade has settled or exited, the market has
  no snapshot within 15 minutes before the report time (`MARK_MAX_AGE`: a fixed bound, as
  reports run on their own schedule; 15 minutes is a few polls of a daily market and one
  15-minute crypto window), the market has closed and awaits settlement, or the snapshot
  recorded no depth for the position's side.

Portfolio and strategy totals (`open_value_usd`, `unrealized_pnl_usd`) sum every marked
trade, contracts without bids at $0; `open_no_bid_contracts` is the number of those
contracts and `open_unmarked` counts the open trades with no fresh book (left out).
`nem summary` adds "N contracts with no bids valued at $0 (pending loss)" when any exist.
A mark is the price of leaving now into the recorded book, not a forecast of the
settlement payout.
