from typing import Any, Literal, Self

from fastapi import APIRouter, Response
from pydantic import BaseModel

from naos_api import policies
from naos_api.models import PolicySnapshot
from naos_api.mounts import MountPolicyIn
from naos_api.routes.deps import SessionDep, SettingsDep
from naos_api.spec import PolicyKind, StrictModel


class MountSnapshotCreate(StrictModel):
    kind: Literal["mount"]
    document: MountPolicyIn


class SnapshotRead(BaseModel):
    id: str
    kind: PolicyKind
    digest: str
    document: dict[str, Any]
    created_at: int

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
