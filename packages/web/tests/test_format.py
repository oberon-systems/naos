import pytest

from naos_web import format

NOW = 1_800_000_000


@pytest.mark.parametrize(
    ("then", "expected"),
    [
        (None, "\u2014"),
        (NOW, "0s ago"),
        (NOW - 30, "30s ago"),
        (NOW - 60, "1m ago"),
        (NOW - 3060, "51m ago"),
        (NOW - 7200, "2h ago"),
        (NOW - 172800, "2d ago"),
    ],
)
def test_ago_reads_the_board(then: int | None, expected: str) -> None:
    assert format.ago(then, NOW) == expected


def test_a_clock_that_ran_backwards_never_reads_negative() -> None:
    assert format.ago(NOW + 5, NOW) == "0s ago"


@pytest.mark.parametrize(
    ("then", "expected"),
    [
        (None, "no heartbeat"),
        (NOW - 2, "hb 2s ago"),
        (NOW - 94, "hb 94s ago"),
        (NOW - 4000, "hb 1h ago"),
    ],
)
def test_a_heartbeat_stays_in_seconds_while_it_is_recent(then: int | None, expected: str) -> None:
    assert format.heartbeat_ago(then, NOW) == expected


@pytest.mark.parametrize(
    ("started", "finished", "expected"),
    [
        (None, None, "\u2014"),
        (NOW - 134, None, "00:02:14"),
        (NOW - 800, None, "00:13:20"),
        (NOW - 5000, NOW - 4589, "00:06:51"),
    ],
)
def test_duration_brackets_the_run(
    started: int | None, finished: int | None, expected: str
) -> None:
    assert format.duration(started, finished, NOW) == expected


def test_a_queued_run_has_no_duration_even_once_it_finishes() -> None:
    assert format.duration(None, NOW, NOW) == "\u2014"


def test_a_lease_says_what_is_left_of_what() -> None:
    assert format.lease_left(NOW - 8, NOW + 52, NOW) == "lease 52s of 60s"
    assert format.lease_percent(NOW - 8, NOW + 52, NOW) == 87


def test_a_lapsed_lease_says_it_fenced_its_runs() -> None:
    assert format.lease_left(None, None, NOW) == "lease expired \u00b7 fenced"
    assert format.lease_percent(None, None, NOW) == 0


def test_an_overdue_lease_never_draws_past_its_bar() -> None:
    assert format.lease_percent(NOW - 60, NOW - 1, NOW) == 0
    assert format.lease_percent(NOW + 10, NOW + 70, NOW) == 100


@pytest.mark.parametrize(
    ("capacity", "held", "expected"),
    [(None, 0, "\u2014 slots"), (2, 2, "2 / 2 slots"), (2, 0, "0 / 2 slots")],
)
def test_slots_read_as_the_board_writes_them(
    capacity: int | None, held: int, expected: str
) -> None:
    assert format.slots(capacity, held) == expected
