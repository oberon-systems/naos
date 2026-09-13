from enum import StrEnum

from transitions import Machine

from naos_api.errors import InvalidTransitionError


class TaskStatus(StrEnum):
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


S = TaskStatus

LIFECYCLE = Machine(
    model=None,
    states=TaskStatus,
    initial=S.PENDING,
    auto_transitions=False,
    transitions=[
        {"trigger": "start", "source": S.PENDING, "dest": S.STARTING},
        {"trigger": "cancel", "source": S.PENDING, "dest": S.CANCELLED},
        {"trigger": "boot", "source": S.STARTING, "dest": S.STARTED},
        {"trigger": "stop", "source": [S.STARTING, S.STARTED], "dest": S.STOPPING},
        {"trigger": "collect", "source": S.STOPPING, "dest": S.COLLECTING},
        {"trigger": "await_merge", "source": S.COLLECTING, "dest": S.WAITING_MERGE},
        {"trigger": "complete", "source": S.WAITING_MERGE, "dest": S.COMPLETED},
        {
            "trigger": "fail",
            "source": [S.PENDING, S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING, S.WAITING_MERGE],
            "dest": S.FAILED,
        },
    ],
)


def targets(current: TaskStatus) -> frozenset[TaskStatus]:
    return frozenset(
        TaskStatus(transition.dest)
        for transition in LIFECYCLE.get_transitions(source=current.name)
        if transition.dest is not None
    )


TERMINAL = frozenset(status for status in TaskStatus if not targets(status))
ACTIVE = frozenset(TaskStatus) - TERMINAL - {TaskStatus.PENDING}


def ensure_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in targets(current):
        raise InvalidTransitionError(f"transition {current} -> {target} is not allowed")
