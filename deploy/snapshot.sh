#!/usr/bin/env bash
# Consistent copy of the live database for NEM Desk (or anyone) to pull with rsync.
# Copying data/nem.db directly while nem-run writes to it can give a corrupt file;
# SQLite's online backup can't. Keeps only the latest snapshot: the disk is small.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/snapshot
tmp=data/snapshot/nem.db.tmp
rm -f "$tmp"
sqlite3 data/nem.db ".backup '$tmp'"
mv -f "$tmp" data/snapshot/nem.db
