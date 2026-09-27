from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Query, Response
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
    profiles: list[str]
    runs_open: int
    runs_total: int

    @classmethod
    def viewed(cls, view: policies.PolicyView) -> Self:
        policy = view.policy
        return cls(
            id=policy.id,
            kind=policy.kind,
            digest=policy.digest,
            document=policy.document,
            created_at=policy.created_at,
            profiles=view.profiles,
            runs_open=view.runs_open,
            runs_total=view.runs_total,
        )


def _read(session: SessionDep, policy: Policy) -> PolicyRead:
    return PolicyRead.viewed(policies.view_policies(session, [policy])[0])


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
    return _read(session, policy)


@router.get("/policies")
def list_policies(
    session: SessionDep,
    kind: PolicyKind | None = None,
    q: Annotated[str | None, Query(max_length=128)] = None,
) -> list[PolicyRead]:
    page = policies.list_policies(session, kind, q)
    return [PolicyRead.viewed(view) for view in policies.view_policies(session, page)]


@router.get("/policies/{policy_id}")
def get_policy(policy_id: str, session: SessionDep) -> PolicyRead:
    return _read(session, policies.get_policy(session, policy_id))
