from typing import Annotated, Literal, Self

from fastapi import APIRouter, Query
from pydantic import BaseModel

from naos_api import runners
from naos_api.clock import NowDep
from naos_api.lifecycle import RunStatus
from naos_api.routes.deps import SessionDep

RunnerStatus = Literal["live", "stale", "revoked"]


class HeldRunRead(BaseModel):
    id: str
    seq: int
    status: RunStatus


class RunnerRead(BaseModel):
    id: str
    name: str
    status: RunnerStatus
    capacity: int | None
    runs: list[HeldRunRead]
    created_at: int
    last_heartbeat_at: int | None
    lease_acquired_at: int | None
    lease_expires_at: int | None
    revoked_at: int | None

    @classmethod
    def of(cls, state: runners.RunnerState) -> Self:
        return cls(
            id=state.runner.id,
            name=state.runner.name,
            status=_status(state),
            capacity=state.runner.capacity,
            runs=[HeldRunRead(id=run.id, seq=run.seq, status=run.status) for run in state.runs],
            created_at=state.runner.created_at,
            last_heartbeat_at=state.runner.last_heartbeat_at,
            lease_acquired_at=state.lease.acquired_at if state.lease else None,
            lease_expires_at=state.lease.expires_at if state.lease else None,
            revoked_at=state.runner.revoked_at,
        )


def _status(state: runners.RunnerState) -> RunnerStatus:
    if state.runner.revoked_at is not None:
        return "revoked"
    return "live" if state.lease is not None else "stale"


router = APIRouter()


@router.get("/runners")
def list_runners(
    session: SessionDep,
    now: NowDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RunnerRead]:
    return [RunnerRead.of(state) for state in runners.list_runners(session, now, limit, offset)]
