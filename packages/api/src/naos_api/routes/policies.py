from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from naos_api import policies
from naos_api.mcp import McpPolicyIn
from naos_api.models import Policy
from naos_api.mounts import MountPolicyIn
from naos_api.network import NetworkPolicyIn
from naos_api.routes.deps import MountRootsDep, SessionDep
from naos_api.shell import ShellPolicyIn
from naos_api.spec import PolicyKind, StrictModel


class MountPolicyCreate(StrictModel):
    kind: Literal["mount"]
    document: MountPolicyIn


class NetworkPolicyCreate(StrictModel):
    kind: Literal["network"]
    document: NetworkPolicyIn


class ShellPolicyCreate(StrictModel):
    kind: Literal["shell"]
    document: ShellPolicyIn


class McpPolicyCreate(StrictModel):
    kind: Literal["mcp"]
    document: McpPolicyIn


PolicyCreate = Annotated[
    MountPolicyCreate | NetworkPolicyCreate | ShellPolicyCreate | McpPolicyCreate,
    Field(discriminator="kind"),
]


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
    body: PolicyCreate, session: SessionDep, roots: MountRootsDep, response: Response
) -> PolicyRead:
    if isinstance(body, MountPolicyCreate):
        policy, created = policies.create_mount_policy(session, body.document, roots)
    elif isinstance(body, NetworkPolicyCreate):
        policy, created = policies.create_network_policy(session, body.document)
    elif isinstance(body, ShellPolicyCreate):
        policy, created = policies.create_shell_policy(session, body.document)
    else:
        policy, created = policies.create_mcp_policy(session, body.document)
    if not created:
        response.status_code = 200
    return PolicyRead.of(policy)


@router.get("/policies/{policy_id}")
def get_policy(policy_id: str, session: SessionDep) -> PolicyRead:
    return PolicyRead.of(policies.get_policy(session, policy_id))
