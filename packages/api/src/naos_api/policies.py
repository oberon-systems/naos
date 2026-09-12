from typing import Any
from uuid import uuid4

from sqlmodel import Session, select

from naos_api.errors import NotFoundError, PolicyError
from naos_api.models import PolicySnapshot
from naos_api.mounts import MountPolicyIn, resolve_mount_policy
from naos_api.settings import Settings
from naos_api.spec import PolicyKind, RunSpec, digest_of

ID_PREFIX = {
    PolicyKind.MOUNT: "mntpol",
    PolicyKind.NETWORK: "netpol",
    PolicyKind.SHELL: "shellpol",
    PolicyKind.MCP: "mcppol",
}


def _find(session: Session, kind: PolicyKind, digest: str) -> PolicySnapshot | None:
    statement = select(PolicySnapshot).where(
        PolicySnapshot.kind == kind, PolicySnapshot.digest == digest
    )
    return session.exec(statement).first()


def _store(
    session: Session, kind: PolicyKind, document: dict[str, Any]
) -> tuple[PolicySnapshot, bool]:
    digest = digest_of(document)
    existing = _find(session, kind, digest)
    if existing is not None:
        return existing, False

    snapshot = PolicySnapshot(
        id=f"{ID_PREFIX[kind]}_{uuid4().hex}", kind=kind, digest=digest, document=document
    )
    session.add(snapshot)
    try:
        session.commit()
    except Exception:
        session.rollback()
        existing = _find(session, kind, digest)
        if existing is None:
            raise
        return existing, False
    return snapshot, True


def create_mount_snapshot(
    session: Session, settings: Settings, policy: MountPolicyIn
) -> tuple[PolicySnapshot, bool]:
    resolved = resolve_mount_policy(policy, settings.allowed_mount_roots)
    return _store(session, PolicyKind.MOUNT, resolved.model_dump(mode="json"))


def get_snapshot(session: Session, snapshot_id: str) -> PolicySnapshot:
    snapshot = session.get(PolicySnapshot, snapshot_id)
    if snapshot is None:
        raise NotFoundError(f"policy snapshot {snapshot_id} does not exist")
    return snapshot


def check_refs(session: Session, spec: RunSpec) -> None:
    for kind, snapshot_id in spec.policy_refs().items():
        if snapshot_id is None:
            continue
        snapshot = session.get(PolicySnapshot, snapshot_id)
        if snapshot is None or snapshot.kind != kind:
            raise PolicyError(f"{kind} policy snapshot {snapshot_id} does not exist")
