from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from naos_api import merges, tasks
from naos_api.clock import NowDep
from naos_api.lifecycle import TaskStatus
from naos_api.models import Merge, Task
from naos_api.routes.deps import IdempotencyKey, SessionDep
from naos_api.spec import RunSpec, StrictModel

MergePath = Annotated[str, Field(min_length=1, max_length=4096)]


class TaskRead(BaseModel):
    id: str
    status: TaskStatus
    status_reason: str | None
    spec: RunSpec
    created_at: int
    updated_at: int

    @classmethod
    def of(cls, task: Task) -> Self:
        return cls(
            id=task.id,
            status=task.status,
            status_reason=task.status_reason,
            spec=RunSpec.model_validate(task.spec),
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


class MergeRead(BaseModel):
    task_id: str
    entries: list[dict[str, Any]]
    decision: dict[str, Any] | None
    conflicts: list[dict[str, Any]] | None
    report: dict[str, Any] | None
    updated_at: int

    @classmethod
    def of(cls, merge: Merge) -> Self:
        return cls(
            task_id=merge.task_id,
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


@router.post("/tasks", status_code=201)
def create_task(
    spec: RunSpec, idempotency_key: IdempotencyKey, session: SessionDep, response: Response
) -> TaskRead:
    task, created = tasks.create_task(session, spec, idempotency_key)
    if not created:
        response.status_code = 200
    return TaskRead.of(task)


@router.get("/tasks")
def list_tasks(
    session: SessionDep,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TaskRead]:
    return [TaskRead.of(task) for task in tasks.list_tasks(session, task_status, limit, offset)]


@router.get("/tasks/{task_id}")
def get_task(task_id: str, session: SessionDep) -> TaskRead:
    return TaskRead.of(tasks.get_task(session, task_id))


@router.post("/tasks/{task_id}/stop")
def stop_task(task_id: str, session: SessionDep) -> TaskRead:
    return TaskRead.of(tasks.stop_task(session, task_id))


@router.get("/tasks/{task_id}/merge")
def get_merge(task_id: str, session: SessionDep) -> MergeRead:
    return MergeRead.of(merges.get_merge(session, task_id))


@router.post("/tasks/{task_id}/merge")
def decide_merge(
    task_id: str, body: MergeDecisionIn, session: SessionDep, now: NowDep
) -> MergeRead:
    return MergeRead.of(merges.decide(session, task_id, body.paths, body.resolutions, now))


@router.post("/tasks/{task_id}/merge/reject")
def reject_merge(task_id: str, session: SessionDep, now: NowDep) -> MergeRead:
    return MergeRead.of(merges.decide(session, task_id, [], {}, now))
