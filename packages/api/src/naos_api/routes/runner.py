from typing import Annotated, Any, Self

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StrictInt, model_validator
from sqlmodel import Session

from naos_api import runners
from naos_api.auth import require_enrollment, require_runner
from naos_api.clock import NowDep
from naos_api.db import get_session
from naos_api.lifecycle import RunStatus
from naos_api.models import Lease
from naos_api.routes.runs import RunRead
from naos_api.runners import IssuedToken, RunnerPrincipal
from naos_api.runs import MAX_REASON_LENGTH
from naos_api.settings import Settings, get_settings
from naos_api.spec import PolicyKind, RunSpec, StrictModel

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
PrincipalDep = Annotated[RunnerPrincipal, Depends(require_runner)]

RunnerName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
Capacity = Annotated[StrictInt, Field(ge=0, le=64)]
Reason = Annotated[str, Field(min_length=1, max_length=MAX_REASON_LENGTH)]


class RegisterIn(StrictModel):
    name: RunnerName


class HeartbeatIn(StrictModel):
    capacity: Capacity


class TransitionIn(StrictModel):
    lease_id: Annotated[str, Field(max_length=64)]
    expected: RunStatus
    target: RunStatus
    reason: Reason | None = None

    @model_validator(mode="after")
    def _failure_needs_reason(self) -> Self:
        if self.target is RunStatus.FAILED and self.reason is None:
            raise ValueError("a FAILED transition requires a reason")
        return self


class TokenOut(BaseModel):
    value: str
    expires_at: int

    @classmethod
    def of(cls, token: IssuedToken) -> Self:
        return cls(value=token.value, expires_at=token.expires_at)


class LeaseOut(BaseModel):
    id: str
    expires_at: int
    ttl_seconds: int

    @classmethod
    def of(cls, lease: Lease, now: int) -> Self:
        return cls(id=lease.id, expires_at=lease.expires_at, ttl_seconds=lease.expires_at - now)


class RegisterOut(BaseModel):
    runner_id: str
    token: TokenOut
    lease: LeaseOut


class HeartbeatOut(BaseModel):
    lease: LeaseOut
    token: TokenOut | None


class DesiredRunOut(BaseModel):
    id: str
    status: RunStatus
    spec: RunSpec
    policies: dict[PolicyKind, dict[str, Any] | None]


class DesiredStateOut(BaseModel):
    lease_id: str
    runs: list[DesiredRunOut]


router = APIRouter(prefix="/runners")


@router.post("/register", status_code=201, dependencies=[Depends(require_enrollment)])
def register(
    body: RegisterIn, session: SessionDep, settings: SettingsDep, now: NowDep
) -> RegisterOut:
    runner, token, lease = runners.register_runner(session, settings, body.name, now)
    return RegisterOut(runner_id=runner.id, token=TokenOut.of(token), lease=LeaseOut.of(lease, now))


@router.post("/{runner_id}/heartbeat")
def heartbeat(
    body: HeartbeatIn,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    now: NowDep,
) -> HeartbeatOut:
    beat = runners.heartbeat(session, settings, principal, body.capacity, now)
    return HeartbeatOut(
        lease=LeaseOut.of(beat.lease, now),
        token=TokenOut.of(beat.token) if beat.token else None,
    )


@router.get("/{runner_id}/runs")
def desired_runs(principal: PrincipalDep, session: SessionDep, now: NowDep) -> DesiredStateOut:
    lease_id, desired = runners.desired_state(session, principal.runner_id, now)
    return DesiredStateOut(
        lease_id=lease_id,
        runs=[
            DesiredRunOut(
                id=item.run.id,
                status=item.run.status,
                spec=RunSpec.model_validate(item.run.spec),
                policies=item.policies,
            )
            for item in desired
        ],
    )


@router.post("/{runner_id}/runs/{run_id}/transition")
def transition(
    run_id: str, body: TransitionIn, principal: PrincipalDep, session: SessionDep, now: NowDep
) -> RunRead:
    run = runners.transition(
        session,
        principal.runner_id,
        run_id,
        body.lease_id,
        body.expected,
        body.target,
        body.reason,
        now,
    )
    return RunRead.of(run)
