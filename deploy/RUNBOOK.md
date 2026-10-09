# Runbook: running NEM 24/7

How to run paper trading, recording and reporting on a Linux server (tested layout:
Ubuntu on a DigitalOcean droplet) with systemd. Paper trading needs no Kalshi account.

## What runs

| Service | Does | Heartbeat name |
|---|---|---|
| `nem-run` | Paper-trades every portfolio in `portfolios/`, and records every market snapshot and result it sees to `data/nem.db` | `runner` |
| `nem-report` | Publishes each portfolio's reports (CSV / Google Sheets) every 5 minutes | `reporter` |

Both read the same SQLite database. Reporting is deliberately its own process, so a
problem in one never stops the other.

## Sizing

- **Memory:** about 50 MB for `nem-run` with any number of portfolios. The smallest
  droplet (512 MB) is enough. Don't start one process per strategy: that's how the old
  system ran out of memory and had processes silently killed.
- **Disk:** about 0.8 KB per market snapshot with 10 orderbook levels. One series polled
  every 5 s is about 13 MB/day (~400 MB/month); every 15 s is a third of that. Fewer depth
  levels (`--depth`) or a slower `check_every` shrink it. Check usage with `du -h data/`.

## First-time setup

```bash
# as root
adduser --disabled-password --gecos "" nem
apt-get update && apt-get install -y git sqlite3

# as nem
sudo -iu nem
git clone https://github.com/ahanqiliu903/not-enough-markets.git
cd not-enough-markets
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.local/bin/env
uv sync --frozen --no-dev            # add --extra sheets for Google Sheets
mkdir -p data out portfolios
uv run nem init                      # or copy your portfolio files into portfolios/
uv run nem validate
uv run nem run --once                # one live tick as a smoke test
exit

# as root: secrets file and services
mkdir -p /etc/nem
cp /home/nem/not-enough-markets/deploy/nem.env.example /etc/nem/nem.env
chown root:nem /etc/nem/nem.env && chmod 640 /etc/nem/nem.env
cp /home/nem/not-enough-markets/deploy/nem-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nem-run nem-report
```

## Day to day

```bash
cd ~/not-enough-markets
.venv/bin/nem status --expect runner,reporter   # health, halts, per-strategy activity
.venv/bin/nem summary                           # statistics
journalctl -u nem-run -f                        # live logs (also: -u nem-report, --since "1 hour ago")
```

`nem status` exits with code 2 if an expected process hasn't sent a heartbeat within
`--max-age` (default 2m), so it can feed any monitoring you add.

**Idle is not dead.** With no open markets (some series don't trade weekends), `nem-run`
still heartbeats every tick and simply finds nothing to do. A strategy that's `paused`
or `retired` in its portfolio file, or halted, is stopped on purpose, and `nem status`
says so.

## Changing strategies

Edit files in `portfolios/`. `nem-run` notices the change within one tick and reloads.
An invalid edit is rejected and logged (`portfolio edit rejected`), and the previous
config keeps running. Check with `nem validate` before saving if in doubt.

- Pause a strategy: `status: paused` (keeps settling its open positions).
- Retire one: `status: retired` (its history stays in reports for comparison).

## Kill switch

```bash
.venv/bin/nem halt --reason "Kalshi API acting up"         # everything
.venv/bin/nem halt research --reason "rebalancing"         # one portfolio
.venv/bin/nem halt research/fav90 --reason "check fills"   # one strategy
.venv/bin/nem resume research/fav90                        # undo (same scope)
```

Halts stop new entries immediately (next tick), across every process sharing the
database, and survive restarts. Open positions still settle. There's no magic file to
touch: `nem status` always shows what's halted and why.

## Updating

```bash
sudo -iu nem
cd not-enough-markets && git pull && uv sync --frozen --no-dev
exit
systemctl restart nem-run nem-report
```

Database schema upgrades run automatically on startup.

## Backups and getting data off the server

```bash
# consistent copy while running (SQLite online backup)
sqlite3 data/nem.db ".backup data/nem-$(date +%F).db"

# portable file for replay elsewhere
.venv/bin/nem export --db data/nem.db --out data/export.jsonl.gz
# then, from your laptop:
scp nem@<server>:not-enough-markets/data/export.jsonl.gz .
```

## Troubleshooting

| Symptom | Check |
|---|---|
| `runner stale` in `nem status` | `systemctl status nem-run`, `journalctl -u nem-run -n 100` |
| Service keeps restarting | `journalctl -u nem-run -n 50`; a bad portfolio file at startup fails `nem validate` |
| `Killed` with no traceback | memory: `journalctl -k \| grep -i oom`, raise `MemoryMax` or the droplet size |
| `runner failing` (alive, errors) | Kalshi or network issue; it retries every poll. Halt if it persists |
| No trades | `nem status` (paused/halted?), `nem summary` (`skipped` shows which gate or risk limit blocked) |
