from typing import Annotated, Any, Self

from fastapi import APIRouter, Query
from pydantic import BaseModel

from naos_api import audit, tasks
from naos_api.models import AuditEvent
from naos_api.routes.deps import SessionDep


class AuditEventRead(BaseModel):
    seq: int
    id: str
    at: int
    source: str
    event: str
    actor: str
    run_id: str | None
    vm_id: str | None
    runner_id: str | None
    data: dict[str, Any]

    @classmethod
    def of(cls, event: AuditEvent) -> Self:
        return cls.model_validate(event, from_attributes=True)


router = APIRouter()


@router.get("/tasks/{task_id}/events")
def task_events(
    task_id: str,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
) -> list[AuditEventRead]:
    tasks.get_task(session, task_id)
    return [AuditEventRead.of(event) for event in audit.timeline(session, task_id, limit)]


@router.get("/audit")
def search_events(
    session: SessionDep,
    runner_id: str | None = None,
    event: str | None = None,
    since: Annotated[int | None, Query(ge=0)] = None,
    after: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[AuditEventRead]:
    found = audit.search(
        session, runner_id=runner_id, event=event, since=since, after=after, limit=limit
    )
    return [AuditEventRead.of(row) for row in found]
