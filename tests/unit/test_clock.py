from datetime import UTC, datetime, timedelta

import pytest

from nem.core.clock import (
    EXCHANGE_TZ,
    ManualClock,
    datetime_to_window_code,
    shift_window_code,
    split_window_id,
    window_code_to_datetime,
)


def test_split_window_id() -> None:
    assert split_window_id("KXBTC15M-26OCT071530") == ("KXBTC15M", "26OCT071530")


@pytest.mark.parametrize(
    "bad", ["KXBTC15M", "-26OCT071530", "KXBTC15M-26oct071530", "KXBTC15M-26OCT071530-T1"]
)
def test_split_window_id_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        split_window_id(bad)


def test_window_code_round_trip() -> None:
    dt = window_code_to_datetime("26OCT071530")
    assert dt == datetime(2026, 10, 7, 15, 30, tzinfo=EXCHANGE_TZ)
    assert datetime_to_window_code(dt) == "26OCT071530"
    assert datetime_to_window_code(dt.astimezone(UTC)) == "26OCT071530"


def test_window_code_rejects_bad_month() -> None:
    with pytest.raises(ValueError):
        window_code_to_datetime("26XYZ071530")


@pytest.mark.parametrize(
    ("code", "n", "expected"),
    [
        ("26OCT071530", -1, "26OCT071515"),
        ("26OCT071530", 2, "26OCT071600"),
        ("26OCT312345", 1, "26NOV010000"),  # month rollover
        ("26DEC312345", 1, "27JAN010000"),  # year rollover
        ("26NOV010145", 1, "26NOV010100"),  # DST ends: 01:45 EDT -> 01:00 EST
        ("26MAR080145", 1, "26MAR080300"),  # DST starts: 02:00 doesn't exist
    ],
)
def test_shift_window_code(code: str, n: int, expected: str) -> None:
    assert shift_window_code(code, n) == expected


def test_manual_clock_only_moves_forward() -> None:
    t0 = datetime(2026, 10, 7, tzinfo=UTC)
    clock = ManualClock(t0)
    clock.advance(timedelta(seconds=5))
    assert clock.now() == t0 + timedelta(seconds=5)
    with pytest.raises(ValueError, match="backwards"):
        clock.set(t0)


def test_manual_clock_rejects_naive_start() -> None:
    with pytest.raises(ValueError):
        ManualClock(datetime(2026, 10, 7))
