from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field, StrictInt, model_validator

from naos_api import audit, consoles, merges, runners
from naos_api.audit import RunnerEventIn
from naos_api.auth import require_enrollment, require_runner
from naos_api.clock import NowDep
from naos_api.lifecycle import RunStatus
from naos_api.models import Lease
from naos_api.routes.deps import (
    ConsoleLimitDep,
    CredentialTtlDep,
    LeaseTtlDep,
    SessionDep,
    TokenTtlDep,
)
from naos_api.routes.runs import RunRead
from naos_api.runners import IssuedToken, RunnerPrincipal
from naos_api.runs import MAX_REASON_LENGTH
from naos_api.spec import PolicyKind, RunSpec, StrictModel

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


LeaseId = Annotated[str, Field(max_length=64)]
DiffPath = Annotated[str, Field(min_length=1, max_length=4096)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Mode = Annotated[StrictInt, Field(ge=0, le=0o7777)]
Note = Annotated[str, Field(min_length=1, max_length=MAX_REASON_LENGTH)]
MAX_ENTRIES = 100_000


class DiffEntryIn(StrictModel):
    path: DiffPath
    change: Literal["created", "modified", "deleted", "renamed", "rejected"]
    kind: Literal["file", "dir", "symlink", "other"]
    from_: DiffPath | None = Field(default=None, alias="from")
    size: Annotated[StrictInt, Field(ge=0)] | None = None
    sha256: Sha256Hex | None = None
    mode: Mode | None = None
    target: Annotated[str, Field(max_length=4096)] | None = None
    reason: Note | None = None
    base_sha256: Sha256Hex | None = None
    base_mode: Mode | None = None
    base_target: Annotated[str, Field(max_length=4096)] | None = None
    sensitive: bool = False


class DiffIn(StrictModel):
    lease_id: LeaseId
    entries: Annotated[list[DiffEntryIn], Field(max_length=MAX_ENTRIES)]


class ConflictIn(StrictModel):
    path: DiffPath
    reason: Note


class MergeResultIn(StrictModel):
    lease_id: LeaseId
    outcome: Literal["applied", "conflict"]
    applied: list[DiffPath] = []
    skipped: list[DiffPath] = []
    exported: list[DiffPath] = []
    backed_up: list[DiffPath] = []
    conflicts: list[ConflictIn] = []

    @model_validator(mode="after")
    def _conflict_names_paths(self) -> Self:
        if self.outcome == "conflict" and not self.conflicts:
            raise ValueError("a conflict outcome lists its conflicts")
        return self


class EventsIn(StrictModel):
    events: Annotated[list[RunnerEventIn], Field(min_length=1, max_length=1000)]


class EventsOut(BaseModel):
    accepted: int
    refused: list[str]


class ConsoleOut(BaseModel):
    offset: int


class TokenOut(BaseModel):
    value: str
    expires_at: int

    @classmethod
    def of(cls, token: IssuedToken) -> Self:
        return cls(value=token.value, expires_at=token.expires_at)


class CredentialOut(BaseModel):
    value: str
    expires_at: int


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
    image_url: str
    policies: dict[PolicyKind, dict[str, Any] | None]
    credentials: dict[str, CredentialOut]
    merge: dict[str, Any] | None


class DesiredStateOut(BaseModel):
    lease_id: str
    runs: list[DesiredRunOut]


router = APIRouter(prefix="/runners")


@router.post("/register", status_code=201, dependencies=[Depends(require_enrollment)])
def register(
    body: RegisterIn,
    session: SessionDep,
    token_ttl: TokenTtlDep,
    lease_ttl: LeaseTtlDep,
    now: NowDep,
) -> RegisterOut:
    runner, token, lease = runners.register_runner(session, body.name, now, token_ttl, lease_ttl)
    return RegisterOut(runner_id=runner.id, token=TokenOut.of(token), lease=LeaseOut.of(lease, now))


@router.post("/{runner_id}/heartbeat")
def heartbeat(
    body: HeartbeatIn,
    principal: PrincipalDep,
    session: SessionDep,
    lease_ttl: LeaseTtlDep,
    token_ttl: TokenTtlDep,
    now: NowDep,
) -> HeartbeatOut:
    beat = runners.heartbeat(session, principal, body.capacity, now, lease_ttl, token_ttl)
    return HeartbeatOut(
        lease=LeaseOut.of(beat.lease, now),
        token=TokenOut.of(beat.token) if beat.token else None,
    )


@router.get("/{runner_id}/runs")
def desired_runs(
    principal: PrincipalDep, session: SessionDep, now: NowDep, credential_ttl: CredentialTtlDep
) -> DesiredStateOut:
    lease_id, desired = runners.desired_state(session, principal.runner_id, now, credential_ttl)
    return DesiredStateOut(
        lease_id=lease_id,
        runs=[
            DesiredRunOut(
                id=item.run.id,
                status=item.run.status,
                spec=RunSpec.model_validate(item.run.spec),
                image_url=item.image_url,
                policies=item.policies,
                credentials={
                    name: CredentialOut(value=issued.value, expires_at=issued.expires_at)
                    for name, issued in item.credentials.items()
                },
                merge=item.merge,
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


@router.post("/{runner_id}/runs/{run_id}/diff")
def report_diff(
    run_id: str, body: DiffIn, principal: PrincipalDep, session: SessionDep, now: NowDep
) -> RunRead:
    entries = [
        entry.model_dump(mode="json", by_alias=True, exclude_none=True) for entry in body.entries
    ]
    run = merges.report_diff(session, principal.runner_id, run_id, body.lease_id, entries, now)
    return RunRead.of(run)


@router.post("/{runner_id}/runs/{run_id}/merge")
def report_merge(
    run_id: str, body: MergeResultIn, principal: PrincipalDep, session: SessionDep, now: NowDep
) -> RunRead:
    applied = body.outcome == "applied"
    report = body.model_dump(include={"applied", "skipped", "exported", "backed_up"})
    conflicts = [conflict.model_dump() for conflict in body.conflicts]
    run = merges.report_merge(
        session,
        principal.runner_id,
        run_id,
        body.lease_id,
        report if applied else None,
        None if applied else conflicts,
        now,
    )
    return RunRead.of(run)


@router.post("/{runner_id}/events")
def report_events(body: EventsIn, principal: PrincipalDep, session: SessionDep) -> EventsOut:
    accepted, refused = audit.ingest(session, principal.runner_id, body.events)
    return EventsOut(accepted=accepted, refused=refused)


@router.post("/{runner_id}/runs/{run_id}/console")
def report_console(
    run_id: str,
    offset: Annotated[int, Query(ge=0)],
    data: Annotated[bytes, Body(media_type="application/octet-stream")],
    principal: PrincipalDep,
    session: SessionDep,
    limit: ConsoleLimitDep,
    now: NowDep,
) -> ConsoleOut:
    held = consoles.append(session, principal.runner_id, run_id, offset, data, limit, now)
    return ConsoleOut(offset=held)
