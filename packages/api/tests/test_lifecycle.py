import pytest

from naos_api.errors import InvalidTransitionError
from naos_api.lifecycle import ACTIVE, TERMINAL, TaskStatus, ensure_transition, targets

S = TaskStatus
EXPECTED = {
    S.PENDING: {S.STARTING, S.CANCELLED, S.FAILED},
    S.STARTING: {S.STARTED, S.STOPPING, S.FAILED},
    S.STARTED: {S.STOPPING, S.FAILED},
    S.STOPPING: {S.COLLECTING, S.FAILED},
    S.COLLECTING: {S.WAITING_MERGE, S.FAILED},
    S.WAITING_MERGE: {S.COMPLETED, S.FAILED},
    S.COMPLETED: set(),
    S.FAILED: set(),
    S.CANCELLED: set(),
}
ALLOWED = [(current, target) for current, targets in EXPECTED.items() for target in targets]
FORBIDDEN = [(c, t) for c in TaskStatus for t in TaskStatus if t not in EXPECTED[c]]


def test_machine_matches_lifecycle() -> None:
    assert {status: set(targets(status)) for status in TaskStatus} == EXPECTED


@pytest.mark.parametrize(("current", "target"), ALLOWED)
def test_allowed_transition_passes(current: TaskStatus, target: TaskStatus) -> None:
    ensure_transition(current, target)


@pytest.mark.parametrize(("current", "target"), FORBIDDEN)
def test_forbidden_transition_fails(current: TaskStatus, target: TaskStatus) -> None:
    with pytest.raises(InvalidTransitionError):
        ensure_transition(current, target)


def test_terminal_and_active_sets() -> None:
    assert frozenset({S.COMPLETED, S.FAILED, S.CANCELLED}) == TERMINAL
    assert frozenset({S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING, S.WAITING_MERGE}) == ACTIVE
