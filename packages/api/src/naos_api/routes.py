from datetime import datetime
from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlmodel import Session

from naos_api import policies, runs
from naos_api.db import get_session
from naos_api.errors import (
    IdempotencyConflictError,
    InvalidTransitionError,
    NotFoundError,
    PolicyError,
)
from naos_api.lifecycle import RunStatus
from naos_api.models import PolicySnapshot, Run
from naos_api.mounts import MountPolicyIn
from naos_api.settings import Settings, get_settings
from naos_api.spec import PolicyKind, RunSpec, StrictModel

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
IdempotencyKey = Annotated[str, Header(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]

_ERROR_STATUS: dict[type[Exception], int] = {
    NotFoundError: 404,
    InvalidTransitionError: 409,
    IdempotencyConflictError: 409,
    PolicyError: 422,
}


def domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=_ERROR_STATUS.get(type(exc), 400))


class RunRead(BaseModel):
    id: str
    status: RunStatus
    status_reason: str | None
    spec: RunSpec
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, run: Run) -> Self:
        return cls(
            id=run.id,
            status=run.status,
            status_reason=run.status_reason,
            spec=RunSpec.model_validate(run.spec),
            created_at=run.created_at,
            updated_at=run.updated_at,
        )


class MountSnapshotCreate(StrictModel):
    kind: Literal["mount"]
    document: MountPolicyIn


class SnapshotRead(BaseModel):
    id: str
    kind: PolicyKind
    digest: str
    document: dict[str, Any]
    created_at: datetime

    @classmethod
    def of(cls, snapshot: PolicySnapshot) -> Self:
        return cls(
            id=snapshot.id,
            kind=snapshot.kind,
            digest=snapshot.digest,
            document=snapshot.document,
            created_at=snapshot.created_at,
        )


router = APIRouter()


@router.post("/runs", status_code=201)
def create_run(
    spec: RunSpec, idempotency_key: IdempotencyKey, session: SessionDep, response: Response
) -> RunRead:
    run, created = runs.create_run(session, spec, idempotency_key)
    if not created:
        response.status_code = 200
    return RunRead.of(run)


@router.get("/runs")
def list_runs(
    session: SessionDep,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RunRead]:
    return [RunRead.of(run) for run in runs.list_runs(session, run_status, limit, offset)]


@router.get("/runs/{run_id}")
def get_run(run_id: str, session: SessionDep) -> RunRead:
    return RunRead.of(runs.get_run(session, run_id))


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str, session: SessionDep) -> RunRead:
    return RunRead.of(runs.stop_run(session, run_id))


@router.post("/policy-snapshots", status_code=201)
def create_policy_snapshot(
    body: MountSnapshotCreate, session: SessionDep, settings: SettingsDep, response: Response
) -> SnapshotRead:
    snapshot, created = policies.create_mount_snapshot(session, settings, body.document)
    if not created:
        response.status_code = 200
    return SnapshotRead.of(snapshot)


@router.get("/policy-snapshots/{snapshot_id}")
def get_policy_snapshot(snapshot_id: str, session: SessionDep) -> SnapshotRead:
    return SnapshotRead.of(policies.get_snapshot(session, snapshot_id))
