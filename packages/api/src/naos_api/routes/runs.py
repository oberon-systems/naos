from typing import Annotated, Self

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel

from naos_api import runs
from naos_api.lifecycle import RunStatus
from naos_api.models import Run
from naos_api.routes.deps import IdempotencyKey, SessionDep
from naos_api.spec import RunSpec


class RunRead(BaseModel):
    id: str
    status: RunStatus
    status_reason: str | None
    spec: RunSpec
    created_at: int
    updated_at: int

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
