from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from naos_api import consoles, merges, runs
from naos_api.clock import NowDep
from naos_api.lifecycle import RunStatus
from naos_api.models import Merge, Run
from naos_api.routes.deps import IdempotencyKey, SessionDep
from naos_api.spec import RunSpec, StrictModel

MergePath = Annotated[str, Field(min_length=1, max_length=4096)]
RunState = Literal["active", "queued", "waiting_merge", "failed"]


class RunnerRef(BaseModel):
    id: str
    name: str


class MergeSummary(BaseModel):
    changed: int
    conflicts: int


class RunRead(BaseModel):
    id: str
    seq: int
    status: RunStatus
    status_reason: str | None
    spec: RunSpec
    profile_id: str | None = None
    lease_id: str | None = None
    workspace: str | None = None
    runner: RunnerRef | None = None
    merge: MergeSummary | None = None
    created_at: int
    updated_at: int
    started_at: int | None = None
    finished_at: int | None = None

    @classmethod
    def of(cls, run: Run) -> Self:
        return cls(
            id=run.id,
            seq=run.seq,
            status=run.status,
            status_reason=run.status_reason,
            spec=RunSpec.model_validate(run.spec),
            profile_id=run.profile_id,
            lease_id=run.lease_id,
            created_at=run.created_at,
            updated_at=run.updated_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )

    @classmethod
    def viewed(cls, view: runs.RunView) -> Self:
        read = cls.of(view.run)
        runner = view.runner
        return read.model_copy(
            update={
                "workspace": view.workspace,
                "runner": RunnerRef(id=runner.id, name=runner.name) if runner else None,
                "merge": None
                if view.changed is None or view.conflicts is None
                else MergeSummary(changed=view.changed, conflicts=view.conflicts),
            }
        )


class RunSummaryRead(BaseModel):
    counts: dict[RunStatus, int]
    open: int
    oldest_pending_at: int | None
    failed_24h: int
    last_failure_reason: str | None

    @classmethod
    def of(cls, summary: runs.RunSummary) -> Self:
        return cls(
            counts={status: summary.counts.get(status, 0) for status in RunStatus},
            open=summary.open,
            oldest_pending_at=summary.oldest_pending_at,
            failed_24h=summary.failed_24h,
            last_failure_reason=summary.last_failure_reason,
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
    spec: RunSpec,
    idempotency_key: IdempotencyKey,
    session: SessionDep,
    response: Response,
    now: NowDep,
) -> RunRead:
    run, created = runs.create_run(session, spec, idempotency_key, now=now)
    if not created:
        response.status_code = 200
    return RunRead.of(run)


@router.get("/runs")
def list_runs(
    session: SessionDep,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    state: RunState | None = None,
    runner: str | None = None,
    image: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RunRead]:
    page = runs.list_runs(session, run_status, state, limit, offset, runner, image)
    return [RunRead.viewed(view) for view in runs.view_runs(session, page)]


# Declared before /runs/{run_id}, which would otherwise take "summary" for an id.
@router.get("/runs/summary")
def run_summary(session: SessionDep) -> RunSummaryRead:
    return RunSummaryRead.of(runs.summary(session))


@router.get("/runs/{run_id}")
def get_run(run_id: str, session: SessionDep) -> RunRead:
    run = runs.get_run(session, run_id)
    return RunRead.viewed(runs.view_runs(session, [run])[0])


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


# The log is read by people, so it carries the text and not the screen.
@router.get("/runs/{run_id}/console")
def console_log(run_id: str, session: SessionDep) -> Response:
    return Response(consoles.timed(session, run_id), media_type="text/plain")
