from typing import Annotated, Literal, Self

from fastapi import APIRouter, Query
from pydantic import BaseModel

from naos_api import runners
from naos_api.clock import NowDep
from naos_api.lifecycle import RunStatus
from naos_api.models import Runner
from naos_api.routes.deps import SessionDep, TokenTtlDep

RunnerStatus = Literal["live", "stale", "revoked"]


class HeldRunRead(BaseModel):
    id: str
    seq: int
    status: RunStatus


class PlacementRead(BaseModel):
    host: str | None
    address: str | None
    zone: str | None
    platform: str | None
    version: str | None
    labels: list[str]


# Only the token's windows go out: its hash stays in the database, its value was never kept.
class RunnerRead(BaseModel):
    id: str
    name: str
    status: RunnerStatus
    capacity: int | None
    runs: list[HeldRunRead]
    created_at: int
    last_heartbeat_at: int | None
    lease_id: str | None
    lease_acquired_at: int | None
    lease_expires_at: int | None
    lease_lapsed_at: int | None
    token_expires_at: int
    token_rotates_at: int
    prev_token_expires_at: int | None
    revoked_at: int | None
    drained_at: int | None
    heartbeat_seconds: int | None
    placement: PlacementRead

    @classmethod
    def of(cls, state: runners.RunnerState, now: int, token_ttl: int) -> Self:
        runner = state.runner
        lease = state.lease or state.lapsed
        previous = runner.prev_token_expires_at
        return cls(
            id=runner.id,
            name=runner.name,
            status=_status(state),
            capacity=runner.capacity,
            runs=[HeldRunRead(id=run.id, seq=run.seq, status=run.status) for run in state.runs],
            created_at=runner.created_at,
            last_heartbeat_at=runner.last_heartbeat_at,
            lease_id=lease.id if lease else None,
            lease_acquired_at=state.lease.acquired_at if state.lease else None,
            lease_expires_at=state.lease.expires_at if state.lease else None,
            lease_lapsed_at=state.lapsed.expires_at if state.lapsed else None,
            token_expires_at=runner.token_expires_at,
            token_rotates_at=runner.token_expires_at - token_ttl // 2,
            prev_token_expires_at=previous if previous is not None and previous > now else None,
            revoked_at=runner.revoked_at,
            drained_at=runner.drained_at,
            heartbeat_seconds=runner.heartbeat_seconds,
            placement=PlacementRead(
                host=runner.host,
                address=runner.address,
                zone=runner.zone,
                platform=runner.platform,
                version=runner.version,
                labels=list(runner.labels or []),
            ),
        )


def _status(state: runners.RunnerState) -> RunnerStatus:
    if state.runner.revoked_at is not None:
        return "revoked"
    return "live" if state.lease is not None else "stale"


def _read(session: SessionDep, runner: Runner, now: int, token_ttl: int) -> RunnerRead:
    return RunnerRead.of(runners.runner_state(session, runner, now), now, token_ttl)


router = APIRouter()


@router.get("/runners")
def list_runners(
    session: SessionDep,
    now: NowDep,
    token_ttl: TokenTtlDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RunnerRead]:
    return [
        RunnerRead.of(state, now, token_ttl)
        for state in runners.list_runners(session, now, limit, offset)
    ]


@router.post("/runners/{runner_id}/revoke")
def revoke_runner(
    runner_id: str, session: SessionDep, now: NowDep, token_ttl: TokenTtlDep
) -> RunnerRead:
    return _read(session, runners.revoke_runner(session, runner_id, now), now, token_ttl)


@router.post("/runners/{runner_id}/drain")
def drain_runner(
    runner_id: str, session: SessionDep, now: NowDep, token_ttl: TokenTtlDep
) -> RunnerRead:
    return _read(session, runners.drain_runner(session, runner_id, now), now, token_ttl)
