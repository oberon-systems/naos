from typing import Any, Literal, Self

from fastapi import APIRouter, Response
from pydantic import BaseModel

from naos_api import policies
from naos_api.models import Policy
from naos_api.mounts import MountPolicyIn
from naos_api.routes.deps import MountRootsDep, SessionDep
from naos_api.spec import PolicyKind, StrictModel


class MountPolicyCreate(StrictModel):
    kind: Literal["mount"]
    document: MountPolicyIn


class PolicyRead(BaseModel):
    id: str
    kind: PolicyKind
    digest: str
    document: dict[str, Any]
    created_at: int

    @classmethod
    def of(cls, policy: Policy) -> Self:
        return cls(
            id=policy.id,
            kind=policy.kind,
            digest=policy.digest,
            document=policy.document,
            created_at=policy.created_at,
        )


router = APIRouter()


@router.post("/policies", status_code=201)
def create_policy(
    body: MountPolicyCreate, session: SessionDep, roots: MountRootsDep, response: Response
) -> PolicyRead:
    policy, created = policies.create_mount_policy(session, body.document, roots)
    if not created:
        response.status_code = 200
    return PolicyRead.of(policy)


@router.get("/policies/{policy_id}")
def get_policy(policy_id: str, session: SessionDep) -> PolicyRead:
    return PolicyRead.of(policies.get_policy(session, policy_id))
