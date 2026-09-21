from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from sqlmodel import Session, col, select

from naos_api import audit
from naos_api.errors import NotFoundError, PolicyError
from naos_api.mcp import McpPolicyIn, resolve_mcp_policy
from naos_api.models import Policy
from naos_api.mounts import MountPolicyIn, resolve_mount_policy
from naos_api.network import NetworkPolicyIn, resolve_network_policy
from naos_api.shell import ShellPolicyIn, resolve_shell_policy
from naos_api.spec import PolicyKind, ProfileSpec, digest_of

ID_PREFIX = {
    PolicyKind.MOUNT: "mntpol",
    PolicyKind.NETWORK: "netpol",
    PolicyKind.SHELL: "shellpol",
    PolicyKind.MCP: "mcppol",
}


def _find(session: Session, kind: PolicyKind, digest: str) -> Policy | None:
    statement = select(Policy).where(Policy.kind == kind, Policy.digest == digest)
    return session.exec(statement).first()


def _store(session: Session, kind: PolicyKind, document: dict[str, Any]) -> tuple[Policy, bool]:
    digest = digest_of(document)
    existing = _find(session, kind, digest)
    if existing is not None:
        return existing, False

    policy = Policy(
        id=f"{ID_PREFIX[kind]}_{uuid4().hex}", kind=kind, digest=digest, document=document
    )
    session.add(policy)
    audit.record(session, "policy_created", actor="operator", policy_id=policy.id, kind=str(kind))
    try:
        session.commit()
    except Exception:
        session.rollback()
        existing = _find(session, kind, digest)
        if existing is None:
            raise
        return existing, False
    return policy, True


def create_mount_policy(
    session: Session, policy: MountPolicyIn, allowed_roots: Sequence[str]
) -> tuple[Policy, bool]:
    resolved = resolve_mount_policy(policy, allowed_roots)
    return _store(session, PolicyKind.MOUNT, resolved.model_dump(mode="json"))


def create_network_policy(session: Session, policy: NetworkPolicyIn) -> tuple[Policy, bool]:
    resolved = resolve_network_policy(policy)
    return _store(session, PolicyKind.NETWORK, resolved.model_dump(mode="json"))


def create_shell_policy(session: Session, policy: ShellPolicyIn) -> tuple[Policy, bool]:
    resolved = resolve_shell_policy(policy)
    return _store(session, PolicyKind.SHELL, resolved.model_dump(mode="json"))


def create_mcp_policy(session: Session, policy: McpPolicyIn) -> tuple[Policy, bool]:
    resolved = resolve_mcp_policy(policy)
    return _store(session, PolicyKind.MCP, resolved.model_dump(mode="json"))


def list_policies(session: Session, kind: PolicyKind | None = None) -> Sequence[Policy]:
    statement = select(Policy)
    if kind is not None:
        statement = statement.where(col(Policy.kind) == kind)
    return session.exec(statement.order_by(col(Policy.created_at).desc(), col(Policy.id))).all()


def get_policy(session: Session, policy_id: str) -> Policy:
    policy = session.get(Policy, policy_id)
    if policy is None:
        raise NotFoundError(f"policy {policy_id} does not exist")
    return policy


def check_refs(session: Session, spec: ProfileSpec) -> None:
    for kind, policy_id in spec.policy_refs().items():
        if policy_id is None:
            continue
        policy = session.get(Policy, policy_id)
        if policy is None or policy.kind != kind:
            raise PolicyError(f"{kind} policy {policy_id} does not exist")
