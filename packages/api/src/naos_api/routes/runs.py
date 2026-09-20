from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from naos_api import merges, runs
from naos_api.clock import NowDep
from naos_api.lifecycle import RunStatus
from naos_api.models import Merge, Run
from naos_api.routes.deps import IdempotencyKey, SessionDep
from naos_api.spec import RunSpec, StrictModel

MergePath = Annotated[str, Field(min_length=1, max_length=4096)]


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


class MergeRead(BaseModel):
    run_id: str
    entries: list[dict[str, Any]]
    decision: dict[str, Any] | None
    conflicts: list[dict[str, Any]] | None
    report: dict[str, Any] | None
    updated_at: int

    @classmethod
    def of(cls, merge: Merge) -> Self:
        return cls(
            run_id=merge.run_id,
            entries=merge.entries,
            decision=merge.decision,
            conflicts=merge.conflicts,
            report=merge.report,
            updated_at=merge.updated_at,
        )


class MergeDecisionIn(StrictModel):
    paths: Annotated[list[MergePath], Field(max_length=100_000)]
    resolutions: dict[MergePath, Literal["skip", "take", "export"]] = {}


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


@router.get("/runs/{run_id}/merge")
def get_merge(run_id: str, session: SessionDep) -> MergeRead:
    return MergeRead.of(merges.get_merge(session, run_id))


@router.post("/runs/{run_id}/merge")
def decide_merge(run_id: str, body: MergeDecisionIn, session: SessionDep, now: NowDep) -> MergeRead:
    return MergeRead.of(merges.decide(session, run_id, body.paths, body.resolutions, now))


@router.post("/runs/{run_id}/merge/reject")
def reject_merge(run_id: str, session: SessionDep, now: NowDep) -> MergeRead:
    return MergeRead.of(merges.decide(session, run_id, [], {}, now))
