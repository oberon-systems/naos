from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import ColumnElement
from sqlmodel import Session, case, col, func, or_, select

from naos_api import audit
from naos_api.errors import NotFoundError, PolicyError
from naos_api.lifecycle import TERMINAL
from naos_api.mcp import McpPolicyIn, resolve_mcp_policy
from naos_api.model import ModelPolicyIn, resolve_model_policy
from naos_api.models import Policy, Profile, Run
from naos_api.mounts import MountPolicyIn, resolve_mount_policy
from naos_api.network import NetworkPolicyIn, resolve_network_policy
from naos_api.shell import ShellPolicyIn, resolve_shell_policy
from naos_api.spec import PolicyKind, ProfileSpec, digest_of

ID_PREFIX = {
    PolicyKind.MOUNT: "mntpol",
    PolicyKind.NETWORK: "netpol",
    PolicyKind.SHELL: "shellpol",
    PolicyKind.MCP: "mcppol",
    PolicyKind.MODEL: "modelpol",
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


def create_model_policy(session: Session, policy: ModelPolicyIn) -> tuple[Policy, bool]:
    resolved = resolve_model_policy(policy)
    return _store(session, PolicyKind.MODEL, resolved.model_dump(mode="json"))


def list_policies(
    session: Session, kind: PolicyKind | None = None, query: str | None = None
) -> Sequence[Policy]:
    statement = select(Policy)
    if kind is not None:
        statement = statement.where(col(Policy.kind) == kind)
    if query:
        needle = query.lower()
        statement = statement.where(
            or_(
                func.lower(col(Policy.id)).contains(needle),
                func.lower(col(Policy.digest)).contains(needle),
            )
        )
    return session.exec(statement.order_by(col(Policy.created_at).desc(), col(Policy.id))).all()


def get_policy(session: Session, policy_id: str) -> Policy:
    policy = session.get(Policy, policy_id)
    if policy is None:
        raise NotFoundError(f"policy {policy_id} does not exist")
    return policy


RUN_COLUMNS = {
    PolicyKind.MOUNT: Run.mount_policy_id,
    PolicyKind.NETWORK: Run.network_policy_id,
    PolicyKind.SHELL: Run.shell_policy_id,
    PolicyKind.MCP: Run.mcp_policy_id,
    PolicyKind.MODEL: Run.model_policy_id,
}


@dataclass(frozen=True)
class PolicyView:
    policy: Policy
    profiles: list[str]
    runs_open: int
    runs_total: int


def names_policy(policy_id: str) -> ColumnElement[bool]:
    return or_(*(col(column) == policy_id for column in RUN_COLUMNS.values()))


# Profile specs are JSON, so the policies they name are read in Python, not queried.
def _naming(session: Session) -> dict[str, list[Profile]]:
    naming: dict[str, list[Profile]] = {}
    for profile in session.exec(select(Profile).order_by(col(Profile.name))).all():
        for policy_id in ProfileSpec.model_validate(profile.spec).policy_refs().values():
            if policy_id is not None:
                naming.setdefault(policy_id, []).append(profile)
    return naming


def profiles_naming(session: Session, policy_id: str) -> list[Profile]:
    return _naming(session).get(policy_id, [])


def view_policies(session: Session, policies: Sequence[Policy]) -> list[PolicyView]:
    if not policies:
        return []
    naming = _naming(session)
    open_run = case((col(Run.status).not_in(TERMINAL), 1), else_=0)
    counts: dict[str, tuple[int, int]] = {}
    for kind, column in RUN_COLUMNS.items():
        ids = [policy.id for policy in policies if policy.kind == kind]
        if not ids:
            continue
        for policy_id, total, opened in session.exec(
            select(column, func.count(), func.sum(open_run))
            .where(col(column).in_(ids))
            .group_by(column)
        ).all():
            counts[str(policy_id)] = (int(total), int(opened or 0))
    return [
        PolicyView(
            policy=policy,
            profiles=[profile.id for profile in naming.get(policy.id, [])],
            runs_total=counts.get(policy.id, (0, 0))[0],
            runs_open=counts.get(policy.id, (0, 0))[1],
        )
        for policy in policies
    ]


def check_refs(session: Session, spec: ProfileSpec) -> None:
    for kind, policy_id in spec.policy_refs().items():
        if policy_id is None:
            continue
        policy = session.get(Policy, policy_id)
        if policy is None or policy.kind != kind:
            raise PolicyError(f"{kind} policy {policy_id} does not exist")
