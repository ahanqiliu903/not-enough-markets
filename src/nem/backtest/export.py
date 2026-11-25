"""A `nem suggest` run as JSON (contract/schema/backtest_run.schema.json), for NEM Desk,
with the manifest that makes it reproducible offline (`nem rerun`).

Test-period metrics are included for the chosen variant only: showing every variant's
test result would invite picking the best one on test, which is exactly the overfitting
the held-out period exists to catch.

The manifest (contract 1.1) holds everything a result depends on: the validated portfolio
with every fee rate resolved (and where each came from), the execution assumptions, the
inputs (holdout, data), a content hash of the replayed rows, and the engine's version.
Reporter settings and feeds' contact User-Agents are left out: they don't change results,
and they can be personal. Nothing comes from the environment.
"""

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import metadata
from pathlib import Path
from typing import Any, cast

from nem.backtest.suggest import Suggestion, suggest
from nem.backtest.sweep import run_sweep, split
from nem.backtest.types import Grid, Period, VariantResult
from nem.core.config import PortfolioConfig
from nem.market.fees import StrategyFees, resolve_fees_offline
from nem.stats.report import SCHEMA_VERSION, iso, stats_fields
from nem.store import Store

# What a rerun must reproduce exactly: every result field (run_id and created_at name
# the run; the manifest describes its inputs).
RESULT_KEYS = (
    "portfolio", "strategy", "series", "status", "min_trades", "grid", "train_window",
    "test_window", "chosen", "score_cents", "reasons", "variants", "fill_estimate",
)  # fmt: skip


class RerunError(ValueError):
    pass


def _period(p: Period) -> dict[str, str | None]:
    return {"start": iso(p.start), "end": iso(p.end)}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def run_id(created_at: datetime, portfolio: str, strategy: str, inputs: object = None) -> str:
    """Time, portfolio, strategy and a short hash of the run's inputs: runs in the same
    second with different config, data or assumptions get different ids."""
    base = f"{created_at:%Y%m%dT%H%M%SZ}-{portfolio}-{strategy}"
    if inputs is None:
        return base
    return f"{base}-{hashlib.sha256(_canonical(inputs).encode()).hexdigest()[:8]}"


def engine_info() -> dict[str, object]:
    """The engine's version and, when it runs from a git checkout, its revision. Never
    fails: no git, or not a checkout, gives null."""
    try:
        version: str | None = metadata.version("nem")
    except metadata.PackageNotFoundError:
        version = None
    revision: str | None = None
    dirty: bool | None = None
    here = Path(__file__).parent
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True, timeout=5
        )
        if rev.returncode == 0:
            revision = rev.stdout.strip() or None
            st = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=here, capture_output=True, text=True, timeout=5,
            )  # fmt: skip
            dirty = bool(st.stdout.strip()) if st.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    return {"name": "nem", "version": version, "git_revision": revision, "git_dirty": dirty}


def _seconds(value: object) -> object:
    """Durations as seconds, which the config reads back exactly (ISO strings it doesn't)."""
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, dict):
        items = cast(dict[str, object], value)
        return {k: _seconds(v) for k, v in items.items()}
    if isinstance(value, list):
        return [_seconds(v) for v in cast(list[object], value)]
    return value


def _public_config(p: PortfolioConfig) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(json.dumps(_seconds(p.model_dump())))
    raw["reporting"] = []  # output settings only (may name a sheet); never change results
    for feed in raw.get("feeds", []):
        feed.pop("user_agent", None)  # a contact address, not an input to the result
    return raw


@dataclass(frozen=True, slots=True)
class SweepRun:
    """Everything `nem suggest` computed, before rendering or saving."""

    portfolio: PortfolioConfig  # fees resolved
    fees: list[StrategyFees]
    strategy: str
    series: str
    grid: Grid
    holdout: float
    min_trades: int
    train: Period
    test: Period
    train_results: list[VariantResult]
    suggestion: Suggestion
    dataset: dict[str, Any]


def sweep_and_suggest(
    data: Store,
    base: PortfolioConfig,
    strategy: str,
    grid: Grid,
    *,
    holdout: float,
    min_trades: int,
) -> SweepRun:
    """Resolve fees from the data (offline), split, sweep and suggest. Deterministic:
    the same config and the same rows give the same result."""
    [portfolio], fees = resolve_fees_offline([base], data.series_fees)
    series = portfolio.strategy(strategy).series
    dataset = data.dataset_identity([series], [f.name for f in portfolio.feeds])
    train, test = split(data, [series], holdout)
    results = run_sweep(data, portfolio, strategy, grid, [train, test])
    s = suggest(grid, results[train], results[test], min_trades=min_trades)
    return SweepRun(
        portfolio, fees, strategy, series, grid, holdout, min_trades, train, test,
        results[train], s, dataset,
    )  # fmt: skip


def manifest(
    run: SweepRun,
    *,
    data_label: str,
    hypothesis: str | None = None,
    notes: str | None = None,
    engine: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """The 1.1 manifest fields of a run (see the module docstring). `data_label` is the
    data path as given (relative paths stay relative); `engine` overrides engine_info()
    (fixtures, which must not change with every commit)."""
    p = run.portfolio
    ds = run.dataset
    return {
        "config": _public_config(p),
        "fees": [f.as_dict() for f in run.fees],
        "execution": {
            "entry_fills": "walk the recorded order book as a taker (IOC)",
            "missing_depth": p.fills.missing_depth,
            "max_snapshot_age_seconds": p.max_snapshot_age.total_seconds(),
        },
        "dataset": {
            "path": data_label,
            "sha256": ds["sha256"],
            "series": ds["series"],
            "feeds": ds["feeds"],
            "first_snapshot": iso(ds["first_snapshot"]),
            "last_snapshot": iso(ds["last_snapshot"]),
            "rows": ds["rows"],
        },
        "inputs": {"holdout": run.holdout},
        "engine": dict(engine) if engine is not None else engine_info(),
        "hypothesis": hypothesis,
        "notes": notes,
    }


def run_data(
    s: Suggestion,
    grid: Grid,
    *,
    portfolio: str,
    strategy: str,
    series: str,
    train: Period,
    test: Period,
    train_results: Sequence[VariantResult],
    min_trades: int,
    created_at: datetime,
    manifest: Mapping[str, object] | None = None,
) -> dict[str, object]:
    chosen = s.variant.name if s.variant is not None else None
    inputs: dict[str, object] | None = None
    if manifest is not None:  # what the result depends on; not which revision ran it
        inputs = {k: v for k, v in manifest.items() if k not in ("engine", "notes")}
        inputs["grid"] = grid
        inputs["min_trades"] = min_trades
    out: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id(created_at, portfolio, strategy, inputs),
        "created_at": iso(created_at),
        "portfolio": portfolio,
        "strategy": strategy,
        "series": series,
        "status": s.status,
        "min_trades": min_trades,
        "grid": [{"path": path, "values": values} for path, values in grid.items()],
        "train_window": _period(train),
        "test_window": _period(test),
        "chosen": chosen,
        "score_cents": None if s.score is None else round(100 * s.score, 2),
        "reasons": s.reasons,
        "variants": [
            {
                "name": r.variant.name,
                "overrides": r.variant.overrides,
                "index": list(r.variant.index),
                "train": stats_fields(r.stats),
                "test": stats_fields(s.test.stats)
                if s.test is not None and r.variant.name == chosen
                else None,
            }
            for r in train_results
        ],
        # Fill-quality range isn't modelled yet: fills always walk the recorded book.
        "fill_estimate": None,
    }
    if manifest is not None:
        out.update(manifest)
    return out


def sweep_run_data(
    run: SweepRun, created_at: datetime, manifest_fields: Mapping[str, object]
) -> dict[str, object]:
    return run_data(
        run.suggestion, run.grid, portfolio=run.portfolio.name, strategy=run.strategy,
        series=run.series, train=run.train, test=run.test, train_results=run.train_results,
        min_trades=run.min_trades, created_at=created_at, manifest=manifest_fields,
    )  # fmt: skip


def save_run(data: dict[str, object], directory: Path) -> Path:
    """Write the run as `<run_id>.json`, never over an existing file: if that name is
    taken, `-2`, `-3`, ... is added to the run id (and the file name). The file appears
    complete or not at all (written to a temp file, then linked into place)."""
    directory.mkdir(parents=True, exist_ok=True)
    base = str(data["run_id"])
    n = 1
    while True:
        rid = base if n == 1 else f"{base}-{n}"
        path = directory / f"{rid}.json"
        tmp = directory / f".{rid}.json.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({**data, "run_id": rid}, indent=2) + "\n")
        try:
            os.link(tmp, path)  # fails if `path` exists: no overwrite, even in a race
        except FileExistsError:
            n += 1
            continue
        finally:
            tmp.unlink()
        data["run_id"] = rid
        return path


def load_run(path: Path) -> dict[str, Any]:
    try:
        run: object = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise RerunError(f"{path}: {e}") from e
    if not isinstance(run, dict):
        raise RerunError(f"{path}: not a saved run")
    doc: dict[str, Any] = dict(run)  # pyright: ignore[reportUnknownArgumentType]
    missing = [k for k in ("config", "dataset", "inputs", *RESULT_KEYS) if k not in doc]
    if missing:
        raise RerunError(
            f"{path}: no manifest ({', '.join(missing)} missing); runs saved before contract"
            " 1.1 can't be rerun"
        )
    return doc


def rerun(saved: Mapping[str, Any], data: Store) -> tuple[dict[str, object], list[str]]:
    """Rerun a saved run from its manifest on `data`: the saved resolved config, grid,
    holdout and min_trades, nothing from today's network or defaults. Raises RerunError if
    the data's rows aren't the ones the run saw. Returns the new run document and the
    result fields that differ (empty: reproduced exactly)."""
    want = saved["dataset"]
    portfolio = PortfolioConfig.model_validate(saved["config"])
    series = portfolio.strategy(saved["strategy"]).series
    found = data.dataset_identity([series], [f.name for f in portfolio.feeds])
    if found["sha256"] != want["sha256"]:
        raise RerunError(
            f"the data differs from the run's: sha256 {found['sha256'][:12]} (rows"
            f" {_canonical(found['rows'])}), saved {str(want['sha256'])[:12]} (rows"
            f" {_canonical(want.get('rows'))}); rerun on the database the run used"
        )
    grid: Grid = {g["path"]: list(g["values"]) for g in saved["grid"]}
    run = sweep_and_suggest(
        data, portfolio, saved["strategy"], grid,
        holdout=float(saved["inputs"]["holdout"]), min_trades=int(saved["min_trades"]),
    )  # fmt: skip
    skip = {*RESULT_KEYS, "schema_version", "run_id", "created_at"}
    fields: dict[str, object] = {k: v for k, v in saved.items() if k not in skip}
    fields["engine"] = engine_info()
    created = datetime.fromisoformat(str(saved["created_at"]).replace("Z", "+00:00"))
    new = sweep_run_data(run, created, fields)
    old_result = json.loads(_canonical({k: saved[k] for k in RESULT_KEYS}))
    new_result = json.loads(_canonical({k: new[k] for k in RESULT_KEYS}))
    differs = [k for k in RESULT_KEYS if old_result[k] != new_result[k]]
    return new, differs
