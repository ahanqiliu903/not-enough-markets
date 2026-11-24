"""The data contract (contract/): schemas match what the engine writes, fixtures are
current and valid, and each broken fixture fails the way the app must report."""

import csv
import filecmp
import importlib.util
import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from nem.stats.report import STRATEGY_COLUMNS, SUMMARY_COLUMNS, TRADE_COLUMNS

CONTRACT = Path(__file__).parents[2] / "contract"
SCHEMAS: dict[str, dict[str, Any]] = {
    p.name: json.loads(p.read_text()) for p in (CONTRACT / "schema").glob("*.json")
}
# jsonschema's stubs leave some generics unknown; keep that contained to these two helpers
_RESOURCES: list[tuple[str, Any]] = [(n, Resource.from_contents(s)) for n, s in SCHEMAS.items()]
_EMPTY: Any = Registry[Any]()
REGISTRY: Any = _EMPTY.with_resources(_RESOURCES)
FIXTURES = CONTRACT / "fixtures"
TABLES = {
    "summary.csv": "summary_row.schema.json",
    "strategies.csv": "strategy_row.schema.json",
    "trades.csv": "trade_row.schema.json",
}


def violations(schema_name: str, instance: object) -> list[str]:
    v: Any = Draft202012Validator(SCHEMAS[schema_name], registry=REGISTRY)
    return [str(e.message) for e in v.iter_errors(instance)]


def coerce(value: str, schema: dict[str, Any]) -> object:
    """CSV text -> the JSON value the schema describes, as the app's reader does.
    Unparseable text stays a string so validation reports it."""
    declared: Any = schema.get("type", "string")
    types = cast("list[str]", declared if isinstance(declared, list) else [declared])
    if value == "" and "null" in types:
        return None
    if "boolean" in types and value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        if "integer" in types:
            return int(value)
        if "number" in types:
            return float(value)
    except ValueError:
        return value
    return value


def read_rows(path: Path, schema_name: str) -> list[dict[str, object]]:
    props = SCHEMAS[schema_name]["properties"]
    with path.open(newline="") as f:
        return [
            {k: coerce(v, props.get(k, {})) for k, v in row.items()} for row in csv.DictReader(f)
        ]


def errors(folder: Path) -> list[str]:
    """Every contract violation in a portfolio's CSV folder, as the app should list them."""
    out: list[str] = []
    for file, schema_name in TABLES.items():
        path = folder / file
        with path.open(newline="") as f:
            header = next(csv.reader(f))
        missing = [c for c in SCHEMAS[schema_name]["required"] if c not in header]
        if missing:
            out.append(f"{file}: missing columns {missing}")
            continue
        rows = read_rows(path, schema_name)
        for i, row in enumerate(rows):
            version = str(row["schema_version"])
            if not version.startswith("1."):
                out.append(f"{file} row {i + 1}: schema_version {version}, expected 1.x")
                continue
            out += [f"{file} row {i + 1}: {m}" for m in violations(schema_name, row)]
        keys = [json.dumps(r, sort_keys=True) for r in rows]
        if len(set(keys)) != len(keys):
            out.append(f"{file}: duplicate rows")
    return out


def test_schemas_are_valid() -> None:
    for schema in SCHEMAS.values():
        Draft202012Validator.check_schema(schema)


def test_schemas_match_engine_columns() -> None:
    for schema_name, columns in [
        ("summary_row.schema.json", SUMMARY_COLUMNS),
        ("strategy_row.schema.json", STRATEGY_COLUMNS),
        ("trade_row.schema.json", TRADE_COLUMNS),
    ]:
        assert SCHEMAS[schema_name]["required"] == columns, schema_name


@pytest.mark.parametrize("fixture", ["basic", "empty"])
def test_good_fixtures_validate(fixture: str) -> None:
    root = FIXTURES / fixture
    assert errors(root / "out" / "fixture") == []
    for file, schema_name in [
        ("summary.json", "summary_report.schema.json"),
        ("status.json", "status.schema.json"),
    ]:
        assert violations(schema_name, json.loads((root / file).read_text())) == []
    for run in (root / "out" / "backtests").glob("*.json"):
        assert violations("backtest_run.schema.json", json.loads(run.read_text())) == []


def test_basic_fixture_covers_each_verdict() -> None:
    rows = read_rows(
        FIXTURES / "basic" / "out" / "fixture" / "strategies.csv", "strategy_row.schema.json"
    )
    assert sorted(str(r["verdict"]) for r in rows) == ["edge", "no_edge", "undecided"]
    (run,) = (FIXTURES / "basic" / "out" / "backtests").glob("*.json")
    assert json.loads(run.read_text())["status"] == "suggested"


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing_column", "strategies.csv: missing columns ['edge_cents']"),
        ("wrong_major_version", "schema_version 2.0, expected 1.x"),
        ("bad_timestamp", "does not match"),
        ("duplicate_rows", "trades.csv: duplicate rows"),
    ],
)
def test_broken_fixtures_fail(case: str, message: str) -> None:
    found = errors(FIXTURES / "broken" / case / "out" / "fixture")
    assert any(message in e for e in found), found


def same_db(a: Path, b: Path) -> bool:
    return a.suffix == ".db" and db_content(a) == db_content(b)


def db_content(path: Path) -> tuple[int, list[str]]:
    conn = sqlite3.connect(path)
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        return version, list(conn.iterdump())
    finally:
        conn.close()


def test_fixtures_are_current(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("gen", CONTRACT / "generate_fixtures.py")
    assert spec is not None and spec.loader is not None
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    gen.generate(tmp_path)
    cmp = filecmp.dircmp(FIXTURES, tmp_path)

    def diffs(d: filecmp.dircmp[str]) -> list[str]:
        # SQLite files are compared by content: their bytes depend on the SQLite library
        # version (a Python patch release can change it), not only on what NEM wrote.
        changed = [f for f in d.diff_files if not same_db(Path(d.left, f), Path(d.right, f))]
        out = [*d.left_only, *d.right_only, *changed]
        for sub in d.subdirs.values():
            out += diffs(sub)
        return out

    assert diffs(cmp) == [], "run `uv run python contract/generate_fixtures.py`"
