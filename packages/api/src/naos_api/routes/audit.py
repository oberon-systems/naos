from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Query
from pydantic import BaseModel

from naos_api import audit, runs
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


@router.get("/runs/{run_id}/events")
def run_events(
    run_id: str,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
) -> list[AuditEventRead]:
    runs.get_run(session, run_id)
    return [AuditEventRead.of(event) for event in audit.timeline(session, run_id, limit)]


@router.get("/audit")
def search_events(
    session: SessionDep,
    runner_id: str | None = None,
    image_id: str | None = None,
    profile_id: str | None = None,
    event: str | None = None,
    since: Annotated[int | None, Query(ge=0)] = None,
    after: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    order: Literal["asc", "desc"] = "asc",
) -> list[AuditEventRead]:
    found = audit.search(
        session,
        runner_id=runner_id,
        event=event,
        since=since,
        after=after,
        limit=limit,
        newest_first=order == "desc",
        image_id=image_id,
        profile_id=profile_id,
    )
    return [AuditEventRead.of(row) for row in found]
