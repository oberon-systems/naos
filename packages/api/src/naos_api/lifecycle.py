from enum import StrEnum

from naos_api.errors import InvalidTransitionError


class RunStatus(StrEnum):
    PENDING = "PENDING"
    STARTING = "STARTING"
    STARTED = "STARTED"
    STOPPING = "STOPPING"
    COLLECTING = "COLLECTING"
    WAITING_MERGE = "WAITING_MERGE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ImageStatus(StrEnum):
    IMPORTING = "IMPORTING"
    READY = "READY"
    FAILED = "FAILED"


TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset({RunStatus.STARTING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.STARTING: frozenset({RunStatus.STARTED, RunStatus.STOPPING, RunStatus.FAILED}),
    RunStatus.STARTED: frozenset({RunStatus.STOPPING, RunStatus.FAILED}),
    RunStatus.STOPPING: frozenset({RunStatus.COLLECTING, RunStatus.FAILED}),
    RunStatus.COLLECTING: frozenset({RunStatus.WAITING_MERGE, RunStatus.FAILED}),
    RunStatus.WAITING_MERGE: frozenset({RunStatus.COMPLETED, RunStatus.FAILED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

TERMINAL = frozenset(status for status, targets in TRANSITIONS.items() if not targets)
ACTIVE = frozenset(RunStatus) - TERMINAL - {RunStatus.PENDING}


def ensure_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in TRANSITIONS[current]:
        raise InvalidTransitionError(f"transition {current} -> {target} is not allowed")
