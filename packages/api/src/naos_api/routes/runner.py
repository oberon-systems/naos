from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StrictInt, model_validator

from naos_api import audit, merges, runners
from naos_api.audit import RunnerEventIn
from naos_api.auth import require_enrollment, require_runner
from naos_api.clock import NowDep
from naos_api.lifecycle import TaskStatus
from naos_api.models import Lease
from naos_api.routes.deps import CredentialTtlDep, LeaseTtlDep, SessionDep, TokenTtlDep
from naos_api.routes.tasks import TaskRead
from naos_api.runners import IssuedToken, RunnerPrincipal
from naos_api.spec import PolicyKind, RunSpec, StrictModel
from naos_api.tasks import MAX_REASON_LENGTH

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
    expected: TaskStatus
    target: TaskStatus
    reason: Reason | None = None

    @model_validator(mode="after")
    def _failure_needs_reason(self) -> Self:
        if self.target is TaskStatus.FAILED and self.reason is None:
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


class DesiredTaskOut(BaseModel):
    id: str
    status: TaskStatus
    spec: RunSpec
    image_url: str
    policies: dict[PolicyKind, dict[str, Any] | None]
    credentials: dict[str, CredentialOut]
    merge: dict[str, Any] | None


class DesiredStateOut(BaseModel):
    lease_id: str
    tasks: list[DesiredTaskOut]


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


@router.get("/{runner_id}/tasks")
def desired_tasks(
    principal: PrincipalDep, session: SessionDep, now: NowDep, credential_ttl: CredentialTtlDep
) -> DesiredStateOut:
    lease_id, desired = runners.desired_state(session, principal.runner_id, now, credential_ttl)
    return DesiredStateOut(
        lease_id=lease_id,
        tasks=[
            DesiredTaskOut(
                id=item.task.id,
                status=item.task.status,
                spec=RunSpec.model_validate(item.task.spec),
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


@router.post("/{runner_id}/tasks/{task_id}/transition")
def transition(
    task_id: str, body: TransitionIn, principal: PrincipalDep, session: SessionDep, now: NowDep
) -> TaskRead:
    task = runners.transition(
        session,
        principal.runner_id,
        task_id,
        body.lease_id,
        body.expected,
        body.target,
        body.reason,
        now,
    )
    return TaskRead.of(task)


@router.post("/{runner_id}/tasks/{task_id}/diff")
def report_diff(
    task_id: str, body: DiffIn, principal: PrincipalDep, session: SessionDep, now: NowDep
) -> TaskRead:
    entries = [
        entry.model_dump(mode="json", by_alias=True, exclude_none=True) for entry in body.entries
    ]
    task = merges.report_diff(session, principal.runner_id, task_id, body.lease_id, entries, now)
    return TaskRead.of(task)


@router.post("/{runner_id}/tasks/{task_id}/merge")
def report_merge(
    task_id: str, body: MergeResultIn, principal: PrincipalDep, session: SessionDep, now: NowDep
) -> TaskRead:
    applied = body.outcome == "applied"
    report = body.model_dump(include={"applied", "skipped", "exported", "backed_up"})
    conflicts = [conflict.model_dump() for conflict in body.conflicts]
    task = merges.report_merge(
        session,
        principal.runner_id,
        task_id,
        body.lease_id,
        report if applied else None,
        None if applied else conflicts,
        now,
    )
    return TaskRead.of(task)


@router.post("/{runner_id}/events")
def report_events(body: EventsIn, principal: PrincipalDep, session: SessionDep) -> EventsOut:
    accepted, refused = audit.ingest(session, principal.runner_id, body.events)
    return EventsOut(accepted=accepted, refused=refused)
